# HS logo scraper and NCES coverage check

Collects high-school logos from [hslogos.com](https://www.hslogos.com/) (used under
our licensing agreements) and measures how much of the US high-school universe
they cover, using the NCES public (CCD) and private (PSS) school directories.

MaxPreps and NFHS are **not** scraped: their terms prohibit it, and
`http_client.py` refuses those hosts outright.

## Setup

Python 3.10+.

```bash
cd scraper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Optional: put a contact address in the User-Agent so the site owner can reach you:

```bash
export SCRAPER_CONTACT="logos-team@yourcompany.example"
```

## Pipeline

Run the commands from the repository root (or from `scraper/`, after dropping the `scraper/` prefix).

| Step | Command | Output |
| --- | --- | --- |
| 1. Scrape logos | `python scraper/scrape_hslogos.py --states IL` | `output/logos/{ST}/{slug}/…`, `output/manifest.csv` |
| 2. NCES master list | `python scraper/build_nces.py` | `output/nces/nces_high_schools.csv` |
| 3. Cross-reference | `python scraper/crossref.py --states IL` | `output/results/*.csv` |

For every state, leave out `--states`:

```bash
python scraper/scrape_hslogos.py      # ~12–15k requests at 1–2 s each; plan for several hours
python scraper/build_nces.py
python scraper/crossref.py
```

### Rerunning and resuming

- **Logos already on disk are never downloaded again.** HTML pages are cached in
  `output/cache/html/`, so a rerun or an interrupted run picks up with almost no
  traffic. Use `--refresh` to re-fetch the HTML and pick up new schools or logos.
  To re-download a changed image, delete its file first.
- `manifest.csv` is **merged**: scraping `--states TX` replaces only the TX rows,
  and the manifest is saved after each state.
- `build_nces.py` finds the latest CCD and PSS release through the NCES site and
  caches the zips in `output/nces/raw/`. To pin a release, pass
  `--ccd-url … --pss-url …`. `output/nces/sources.json` records which files were used.
- Other flags: `--limit N` (first N schools per state, for testing),
  `--min-delay/--max-delay` (seconds between requests, default 1–2), `-v`.
- Failed URLs are listed in `output/scrape_errors.csv`. Rerunning retries them.

### Politeness

- Checks `robots.txt` before every request and honours `Crawl-delay`. hslogos.com
  currently has no robots.txt (404), which means no restrictions.
- Sends a descriptive User-Agent (`HSLogoCoverageBot/1.0 …`).
- Waits a random 1–2 s between requests to the same host.
- Retries timeouts, connection errors and HTTP 429/5xx up to 5 times with exponential
  backoff, and honours `Retry-After`.

## How the site is crawled

```
search_schools.html
  └─ States/{State}/search_{st}.html              state index
       └─ search_{st}_a-b.html …                    letter-range indexes (some states)
            └─ {ST}_Schools/{letter}_schools/{slug}.html      school page
                 └─ ../../{ST}_Images/{letter}_images/{School Mascot N}.png|.jpg
```

Each school page lists School, Mascot, State, County and Town, followed by one or
more logos. The first is the primary logo; the rest are labelled, e.g.
"Alternate Logo". Images can be `.png` or `.jpg`, and their filenames contain spaces,
so URLs are decoded and then re-encoded once. Files keep their original names:
`output/logos/IL/abingdon-avon/Abingdon-Avon Tornadoes 1.jpg`.

As of Nov 2026 the site lists 47 states. Michigan, Mississippi and Virginia have no
pages, so they show 0% coverage.

### manifest.csv

`state, school_name, mascot, school_page_url, image_url, local_path, logo_index,
image_width, image_height, sha256, scraped_at`, plus `city`, `county` and `logo_label`
from the school page. `local_path` is relative to `output/`. `logo_index` is the
logo's position on the school page (1 = primary). `scraped_at` is when the file was
first downloaded (UTC).

## NCES master list

- **Public:** CCD school directory (file 029). Keeps schools that offer any of
  grades 9–12 and are not Closed, Inactive or Future. Uses the location state, so
  Bureau of Indian Education schools land in their real state.
- **Private:** PSS public-use file. Keeps schools whose grade range overlaps 9–12.
  PSS is a survey frame, so a few small private schools may be missing.
- Covers the 50 states plus DC. Pass `--include-territories` to add PR, GU and the
  other territories.
- Columns: `nces_id` (12-digit NCESSCH, or 8-character PSS PPIN), `name`, `city`,
  `state`, `zip`, `school_type` (e.g. `Public - Regular School (Charter)`,
  `Private`), `grade_span`, `source`, plus `district`, `charter` and `level`.

## Matching (crossref.py)

1. Names are normalised in `names.py`: case, accents and punctuation; "High School",
   "HS", "H S", "Senior", "Sr", "Jr/Sr", "Community", "Township"; "St." → "Saint",
   "Mt." → "Mount", "Prep" → "Preparatory", and so on.
2. Within the same state, rapidfuzz scores every NCES school. The score is the
   average of `token_sort_ratio` and `token_set_ratio`.
3. Candidates within 5 points of the best are a tie, and the hslogos page's **Town**
   breaks it, which separates the many "Central High School"s. Confidence is the
   name score, +5 when the town matches the NCES city and −10 when it doesn't.
4. Confidence of 90 or more goes to `matched.csv`. From 60 to 90, or when two
   candidates can't be told apart, the school goes to `needs_review.csv`, which
   lists the top 3 candidates. Below 60 it is reported as `no_match`. If two hslogos
   schools claim the same NCES school, the weaker one goes to review as
   `duplicate_target`.

### Results (`output/results/`)

| File | Contents |
| --- | --- |
| `matched.csv` | NCES record, hslogos name, town and page, `logo_count`, `logo_paths` (`;`-separated), `match_confidence`, `name_score`, `city_match` |
| `needs_review.csv` | hslogos schools with low-confidence, ambiguous, duplicate or no match, and their top candidates |
| `missing_logos.csv` | NCES high schools with no matched logo, sorted by state and city. `possible_hslogos_match` names a review-queue school that might cover it. |
| `coverage_by_state.csv` | Totals, schools with logos and percentage per state, plus a TOTAL row. "Core" counts only regular public (including charter) and private schools, leaving out alternative, special-education and CTE centres. |

Once you have confirmed matches in `needs_review.csv`, the simplest fix is to
improve `names.py` or rename the school, then rerun `crossref.py`. It takes seconds.

## Tests

```bash
python -m pytest scraper/tests
```

## What is committed

Code, `manifest.csv` and `output/results/` are committed. Logo images, the HTML cache
and the NCES downloads are git-ignored: they are large, licensed or easy to regenerate.
