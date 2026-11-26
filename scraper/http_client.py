"""Polite HTTP client: robots.txt, descriptive User-Agent, rate limiting, retries."""

from __future__ import annotations

import logging
import os
import random
import time
import urllib.robotparser
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

DEFAULT_CONTACT = os.environ.get("SCRAPER_CONTACT", "")
USER_AGENT = (
    "HSLogoCoverageBot/1.0 (licensed high-school logo coverage audit; "
    "runs at ~1 request every 1-2 s"
    + (f"; contact: {DEFAULT_CONTACT}" if DEFAULT_CONTACT else "")
    + ")"
)

RETRY_STATUSES = {429, 500, 502, 503, 504}

# Hosts we must never crawl, whatever a caller passes in (their terms prohibit it).
BLOCKED_HOSTS = ("maxpreps.com", "nfhs.org", "nfhsnetwork.com")


class RobotsDisallowed(Exception):
    pass


class PoliteSession:
    def __init__(
        self,
        min_delay: float = 1.0,
        max_delay: float = 2.0,
        max_retries: int = 5,
        timeout: float = 60.0,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.request_count = 0

    # -- robots.txt ---------------------------------------------------------
    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            robots_url = origin + "/robots.txt"
            rp = urllib.robotparser.RobotFileParser(robots_url)
            try:
                resp = self._request("GET", robots_url, check_robots=False)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    log.info("Loaded %s", robots_url)
                elif resp.status_code in (401, 403):
                    rp.disallow_all = True
                    log.warning("%s returned %s: treating site as disallowed", robots_url, resp.status_code)
                else:
                    # 404 etc: no robots.txt means no restrictions.
                    rp.allow_all = True
                    log.info("%s returned %s: no restrictions", robots_url, resp.status_code)
            except requests.RequestException as exc:
                log.warning("Could not fetch %s (%s); proceeding as allowed", robots_url, exc)
                rp.allow_all = True
            self._robots[origin] = rp
            delay = rp.crawl_delay(self.user_agent) if hasattr(rp, "crawl_delay") else None
            if delay and float(delay) > self.min_delay:
                log.info("Honouring Crawl-delay %s for %s", delay, origin)
                self.min_delay = float(delay)
                self.max_delay = max(self.max_delay, self.min_delay + 1)
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is None or rp.can_fetch(self.user_agent, url)

    # -- requests -----------------------------------------------------------
    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = random.uniform(self.min_delay, self.max_delay) - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _request(self, method: str, url: str, check_robots: bool = True, **kw) -> requests.Response:
        host = urlsplit(url).netloc.lower()
        if any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS):
            raise ValueError(f"Refusing to fetch {host}: its terms prohibit scraping")
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed(url)
        kw.setdefault("timeout", self.timeout)
        attempt = 0
        while True:
            self._throttle(host)
            self.request_count += 1
            try:
                resp = self.session.request(method, url, **kw)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    raise
                backoff = self._backoff(attempt)
                log.warning("%s on %s; retry %d in %.0fs", type(exc).__name__, url, attempt + 1, backoff)
                time.sleep(backoff)
                attempt += 1
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                backoff = self._retry_after(resp) or self._backoff(attempt)
                log.warning("HTTP %s on %s; retry %d in %.0fs", resp.status_code, url, attempt + 1, backoff)
                time.sleep(backoff)
                attempt += 1
                continue
            return resp

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(120.0, 2.0 ** (attempt + 1)) + random.uniform(0, 1)

    @staticmethod
    def _retry_after(resp: requests.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        try:
            return min(300.0, float(value))
        except ValueError:
            try:
                return max(0.0, min(300.0, parsedate_to_datetime(value).timestamp() - time.time()))
            except (TypeError, ValueError):
                return None

    def get(self, url: str, **kw) -> requests.Response:
        return self._request("GET", url, **kw)

    def download(self, url: str, dest: str, chunk: int = 1 << 16) -> None:
        """Stream url to dest atomically (via a .part file)."""
        resp = self._request("GET", url, stream=True)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            for block in resp.iter_content(chunk):
                fh.write(block)
        os.replace(tmp, dest)
