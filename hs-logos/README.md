# HS logos

Collects high-school logos (used under our licensing agreements), packages one
primary logo per Hudl school as `<STATE>/<hudl_id>.<ext>` with a `logo-index.csv`,
and measures coverage against the full NCES list of US high schools.

MaxPreps and NFHS are never used: their terms prohibit automated collection, and
`http_client.py` refuses those hosts outright.

## Setup

Python 3.10+.

```bash
cd hs-logos
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Optional: put a contact address in the User-Agent (`HSLogoCollector/1.0 …`):

```bash
export LOGO_COLLECTOR_CONTACT="logos-team@yourcompany.example"
```

## Steps

Run the commands from the repository root.

| Step | Command | Output (under `hs-logos/output/`) |
| --- | --- | --- |
| 1. Collect logos | `python hs-logos/collect_logos.py --states IL` | `logos/{ST}/{school}/…`, `manifest.csv` |
| 2. Package for Hudl | `python hs-logos/package_logos.py --schools hudl_schools.csv --states IL` | `packages/HS Logos IL.zip`, `packages/logo-index IL.csv` |
| 3. NCES school list | `python hs-logos/school_list.py` | `nces/nces_high_schools.csv` |
| 4. Coverage | `python hs-logos/match_schools.py --states IL` | `results/*.csv` |

Steps 3 and 4 are optional. They measure how much of the US high-school universe the
logos cover, independent of Hudl's list.

## 1. Collecting logos (`collect_logos.py`)

- Goes one school at a time: about one request every 2–4 s, with a descriptive
  User-Agent. It checks `robots.txt` (honouring `Crawl-delay`) and retries
  timeouts, 429 and 5xx responses with backoff, honouring `Retry-After`.
- Downloads only the **primary logo** for each school by default. That is the
  first mark on the school page, its athletic logo. Pass `--all-logos` to also
  download alternate marks.
- Can resume: files already on disk are skipped, and pages are cached in
  `output/cache/html/`; pass `--refresh` to re-fetch them. `manifest.csv` is merged
  state by state. Failed URLs go to `output/fetch_errors.csv`, and a rerun retries them.
- Other flags: `--limit N` (first N schools per state, for testing) and
  `--min-delay/--max-delay`.

`manifest.csv` columns: `state, school_name, mascot, school_page_url, image_url,
local_path, logo_index, image_width, image_height, sha256, collected_at, city,
county, logo_label`. `logo_index` 1 is the primary mark.

Images are `.png`, `.jpg`, `.jpeg`, `.gif` or `.bmp`, saved unedited under their
original file names. As of Nov 2026 the source lists 47 states; Michigan,
Mississippi and Virginia are missing.

## 2. Packaging for Hudl (`package_logos.py`)

Input: the Hudl school CSV, with columns `hudl_id, state, full_name, short_name,
abbreviation, mascot, city, zip, country, hudl_has_image, hidden`.

- Rows with `hidden` = `yes` are skipped. `hudl_id` is copied exactly.
- A logo is taken only when the school is confirmed:
  - **Name:** close to the Hudl `full_name` or `short_name` after normalising, in the same state.
  - **City:** the logo page's town matches the Hudl city.
  - **Mascot:** matches too, when both sides list one ("Knights" = "Knight").
    A looser name match is accepted when city and mascot both agree, e.g.
    "Central High School" in Champaign with the Maroons.
- Statuses:

  | Status | Meaning |
  | --- | --- |
  | `found` | One confirmed school. Its primary logo is copied unedited to `<STATE>/<hudl_id>.<ext>`. |
  | `ambiguous` | Several schools pass, or the name fits but the city or mascot doesn't. The note says which. Nothing is guessed. |
  | `not found` | No close name in that state. |
  | `unavailable` | Matched, but the image file won't open. |

- If the largest file is under 128 px on its shorter side, the note says so.
- Output per state goes to `output/packages/`:
  - `HS Logos IL.zip`, containing the `IL/` folder and `logo-index.csv` at the top level.
  - When a state exceeds 25 MB, the zips are split into `HS Logos IL 01.zip`, `02`
    and so on, each with its own index. Rows without a file are listed in the first part.
  - `logo-index IL.csv` lists every row of the state in one file.
- Index columns: `hudl_id,state,school,filename,source,status,notes`. `source` is
  the page the logo came from; pass `--no-source` to leave it empty.
- The log line for each state gives the counts of found, not found, ambiguous and unavailable.

## 3. NCES school list (`school_list.py`)

- **Public:** the CCD school directory (file 029). Keeps schools that offer any of
  grades 9–12 and aren't Closed, Inactive or Future, located by their real state.
- **Private:** the PSS public-use file. Keeps schools whose grade range overlaps 9–12.
- Covers the 50 states plus DC; pass `--include-territories` to add the territories.
- Downloads the latest release found on nces.ed.gov and caches it in `output/nces/raw/`.
  Pin a release with `--ccd-url` and `--pss-url`.
- Columns: `nces_id, name, city, state, zip, school_type, grade_span, source,
  district, charter, level`.

## 4. Coverage (`match_schools.py`)

Each logo school is matched to an NCES school.

- Names are normalised in `names.py`: grade ranges, "High School", "HS", "Senior",
  "Jr/Sr", "Community", "Township", "Charter"; "St." → "Saint", "Mt." → "Mount", and so on.
- Names are compared with rapidfuzz within the state, and the town breaks ties.
- Scores are adjusted:
  - Town: +5 when it matches the NCES city, −10 when it doesn't.
  - Religious marker: −15 when a name has one (Lutheran, Catholic, …) and the
    candidate is a public school without it.
  - Containment: a score of at least 92 when one name contains the other and it's
    the only such school in town.
- Confidence of 90 or more with no town conflict goes to `matched.csv`. Everything
  else goes to `needs_review.csv` with the top 3 candidates and a `reason`:
  `low_confidence`, `ambiguous`, `city_mismatch`, `duplicate_target` or `no_match`.
- `missing_logos.csv` lists NCES high schools without a logo, by state.
- `coverage_by_state.csv` gives totals, schools with logos and percentage per state.
  "Core" counts only regular public, charter and private schools.

All thresholds are constants at the top of each script.

## Tests

```bash
python -m pytest hs-logos/tests
```

## What is committed

Code, `manifest.csv` and `output/results/` are committed. Logo images, packages, the
HTML cache and the NCES downloads are git-ignored: they are large, licensed or easy to
regenerate.
