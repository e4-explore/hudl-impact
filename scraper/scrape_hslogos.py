#!/usr/bin/env python3
"""Part 1: collect school logos from hslogos.com.

Crawl: search_schools.html -> state index (and its letter-range sub-indexes)
-> each school page -> every logo image on that page.

Resumable: HTML pages are cached under output/cache/html and images already on
disk are not downloaded again. manifest.csv is merged, so scraping one state
later never drops rows for states scraped earlier.

    python scraper/scrape_hslogos.py --states IL
    python scraper/scrape_hslogos.py            # every state on the site
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from http_client import PoliteSession, RobotsDisallowed

log = logging.getLogger("hslogos")

BASE = "https://www.hslogos.com/"
START_URL = BASE + "search_schools.html"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output")

MANIFEST_FIELDS = [
    "state", "school_name", "mascot", "school_page_url", "image_url", "local_path",
    "logo_index", "image_width", "image_height", "sha256", "scraped_at",
    # Extra context from the school page, used for city tie-breaking in crossref.
    "city", "county", "logo_label",
]


@dataclass
class School:
    state: str
    url: str
    slug: str
    name: str = ""
    mascot: str = ""
    city: str = ""
    county: str = ""
    images: list[tuple[str, str]] = field(default_factory=list)  # (image_url, label)


# --------------------------------------------------------------------------- helpers
def normalize_url(href: str, page_url: str) -> str:
    """Resolve href against page_url and percent-encode the path consistently.

    hslogos mixes raw spaces and %20 in src attributes, so decode first and
    re-encode once.
    """
    absolute = urljoin(page_url, href.strip())
    parts = urlsplit(absolute)
    path = quote(unquote(parts.path), safe="/-_.~()'!*,&+=:@")
    return f"{parts.scheme}://{parts.netloc}{path}"


def safe_component(name: str) -> str:
    """Make a filename/dirname safe while keeping it recognisable."""
    name = name.replace("/", "_").replace("\\", "_").strip()
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name)
    return name or "unnamed"


class Crawler:
    def __init__(self, out_dir: str, session: PoliteSession, refresh: bool = False) -> None:
        self.out_dir = out_dir
        self.session = session
        self.refresh = refresh
        self.cache_dir = os.path.join(out_dir, "cache", "html")
        self.errors: list[tuple[str, str]] = []

    # -- page fetching with on-disk cache -----------------------------------
    def fetch_html(self, url: str) -> str | None:
        rel = urlsplit(url).path.lstrip("/") or "index.html"
        cache_path = os.path.join(self.cache_dir, unquote(rel))
        if not self.refresh and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as fh:
                return fh.read()
        try:
            resp = self.session.get(url)
        except RobotsDisallowed:
            log.warning("robots.txt disallows %s; skipping", url)
            self.errors.append((url, "robots_disallowed"))
            return None
        except Exception as exc:  # network failure after retries
            log.error("Failed %s: %s", url, exc)
            self.errors.append((url, str(exc)))
            return None
        if resp.status_code != 200:
            log.warning("HTTP %s for %s", resp.status_code, url)
            self.errors.append((url, f"HTTP {resp.status_code}"))
            return None
        resp.encoding = resp.encoding if resp.encoding and resp.encoding.lower() != "iso-8859-1" else "utf-8"
        text = resp.text
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return text

    # -- crawl steps ---------------------------------------------------------
    def list_states(self) -> dict[str, str]:
        """Return {ST: state index URL} from the search-by-state page."""
        html = self.fetch_html(START_URL)
        if html is None:
            raise SystemExit("Could not load " + START_URL)
        soup = BeautifulSoup(html, "html.parser")
        states: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            m = re.match(r"States/([^/]+)/search_([a-z]{2})\.html$", a["href"].strip())
            if m:
                states[m.group(2).upper()] = normalize_url(a["href"], START_URL)
        return states

    def list_schools(self, st: str, state_url: str) -> list[School]:
        """Walk the state index and any letter-range sub-indexes for school links."""
        state_dir = state_url.rsplit("/", 1)[0] + "/"
        index_re = re.compile(r"search_%s[^/]*\.html$" % st.lower())
        queue, seen = [state_url], {state_url}
        schools: dict[str, School] = {}
        while queue:
            page = queue.pop(0)
            html = self.fetch_html(page)
            if html is None:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                url = normalize_url(href, page)
                if not url.startswith(state_dir):
                    continue
                if "_Schools/" in url and url.endswith(".html"):
                    if url not in schools:
                        slug = unquote(url.rsplit("/", 1)[1])[: -len(".html")]
                        schools[url] = School(state=st, url=url, slug=slug, name=a.get_text(" ", strip=True))
                elif index_re.search(url) and url not in seen:
                    seen.add(url)
                    queue.append(url)
        log.info("%s: %d index pages, %d school pages", st, len(seen), len(schools))
        return list(schools.values())

    def parse_school(self, school: School) -> bool:
        html = self.fetch_html(school.url)
        if html is None:
            return False
        soup = BeautifulSoup(html, "html.parser")

        # Info panel: <h4><u>School</u></h4><p>...</p> pairs.
        info: dict[str, str] = {}
        aside = soup.find("asidecontent") or soup
        for h4 in aside.find_all("h4"):
            p = h4.find_next_sibling("p")
            if p is not None:
                info[h4.get_text(strip=True).lower()] = p.get_text(" ", strip=True)
        header = soup.find("listheader")
        school.name = info.get("school") or (header.get_text(" ", strip=True) if header else "") or school.name
        school.mascot = info.get("mascot", "")
        school.city = info.get("town") or info.get("city", "")
        school.county = info.get("county", "")

        content = soup.find("listcontent") or soup
        seen: set[str] = set()
        for img in content.find_all("img", src=True):
            src = img["src"]
            if "_Images/" not in src:
                continue  # skip site chrome such as hsl_official.png
            url = normalize_url(src, school.url)
            if url in seen:
                continue
            seen.add(url)
            label = ""
            td = img.find_parent("td")
            tr = td.find_parent("tr") if td else None
            if td is not None and tr is not None:
                idx = tr.find_all("td").index(td)
                next_tr = tr.find_next_sibling("tr")
                if next_tr is not None:
                    cells = next_tr.find_all("td")
                    if idx < len(cells):
                        label = cells[idx].get_text(" ", strip=True)
            school.images.append((url, label or ("Primary Logo" if not school.images else "")))
        return True

    def download_images(self, school: School, previous: dict[str, dict]) -> list[dict]:
        rows = []
        school_dir = os.path.join(self.out_dir, "logos", school.state, safe_component(school.slug))
        for i, (url, label) in enumerate(school.images, start=1):
            filename = safe_component(unquote(url.rsplit("/", 1)[1]))
            dest = os.path.join(school_dir, filename)
            scraped_at = None
            if not os.path.exists(dest):
                try:
                    self.session.download(url, dest)
                    scraped_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                except RobotsDisallowed:
                    self.errors.append((url, "robots_disallowed"))
                    continue
                except Exception as exc:
                    log.error("Image failed %s: %s", url, exc)
                    self.errors.append((url, str(exc)))
                    continue
            else:
                prev = previous.get(url)
                scraped_at = prev["scraped_at"] if prev and prev.get("scraped_at") else (
                    dt.datetime.fromtimestamp(os.path.getmtime(dest), dt.timezone.utc).isoformat(timespec="seconds")
                )
            width = height = ""
            try:
                with Image.open(dest) as im:
                    width, height = im.size
            except Exception as exc:
                log.warning("Not a readable image: %s (%s)", dest, exc)
            with open(dest, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            rows.append({
                "state": school.state,
                "school_name": school.name,
                "mascot": school.mascot,
                "school_page_url": school.url,
                "image_url": url,
                "local_path": os.path.relpath(dest, self.out_dir),
                "logo_index": i,
                "image_width": width,
                "image_height": height,
                "sha256": digest,
                "scraped_at": scraped_at,
                "city": school.city,
                "county": school.county,
                "logo_label": label,
            })
        return rows


# --------------------------------------------------------------------------- manifest
def read_manifest(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_manifest(path: str, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (r["state"], r["school_name"].lower(), r["school_page_url"], int(r["logo_index"])))
    tmp = path + ".part"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--states", nargs="*", help="Two-letter codes to scrape (default: all on the site)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Output directory (default: scraper/output)")
    ap.add_argument("--min-delay", type=float, default=1.0)
    ap.add_argument("--max-delay", type=float, default=2.0)
    ap.add_argument("--refresh", action="store_true", help="Re-fetch HTML pages instead of using the cache")
    ap.add_argument("--limit", type=int, default=0, help="Max schools per state (for testing)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    os.makedirs(args.out, exist_ok=True)
    session = PoliteSession(min_delay=args.min_delay, max_delay=args.max_delay)
    log.info("User-Agent: %s", session.user_agent)
    crawler = Crawler(args.out, session, refresh=args.refresh)

    manifest_path = os.path.join(args.out, "manifest.csv")
    existing = read_manifest(manifest_path)
    previous = {r["image_url"]: r for r in existing}

    states = crawler.list_states()
    log.info("Site lists %d states: %s", len(states), " ".join(sorted(states)))
    wanted = [s.upper() for s in args.states] if args.states else sorted(states)
    unknown = [s for s in wanted if s not in states]
    if unknown:
        log.warning("Not on hslogos.com: %s", " ".join(unknown))
    wanted = [s for s in wanted if s in states]

    kept = [r for r in existing if r["state"] not in wanted]
    new_rows: list[dict] = []
    for st in wanted:
        schools = crawler.list_schools(st, states[st])
        if args.limit:
            schools = schools[: args.limit]
        for n, school in enumerate(schools, 1):
            if not crawler.parse_school(school):
                continue
            rows = crawler.download_images(school, previous)
            new_rows.extend(rows)
            if not school.images:
                log.warning("No logos on %s", school.url)
            if n % 25 == 0 or n == len(schools):
                log.info("%s: %d/%d schools, %d logos so far, %d HTTP requests",
                         st, n, len(schools), len(new_rows), session.request_count)
        # Persist after each state so an interrupted full run keeps its progress.
        write_manifest(manifest_path, kept + new_rows)

    write_manifest(manifest_path, kept + new_rows)
    if crawler.errors:
        err_path = os.path.join(args.out, "scrape_errors.csv")
        with open(err_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["url", "error"])
            w.writerows(crawler.errors)
        log.warning("%d errors written to %s", len(crawler.errors), err_path)
    log.info("Done: %d logo rows for %s; manifest at %s", len(new_rows), " ".join(wanted), manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
