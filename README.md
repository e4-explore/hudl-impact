# High-school logos

Licensed high-school logos for internal use, one state per folder.

```
IL/
  abingdon-avon-high-school.jpg     primary athletic mark, unedited
  ...
  logo-index.csv                    one row per school
tools/                              scripts that collect, check and package the logos
```

Images are stored with **Git LFS**. Install it once (`git lfs install`) before you clone,
or the images arrive as small pointer files.

## logo-index.csv

| Column | Meaning |
| --- | --- |
| `state`, `school`, `mascot`, `city`, `county` | As listed for the school |
| `filename` | Path of the logo in this repo |
| `width`, `height`, `sha256` | Image size in pixels, and a checksum of the file |
| `nces_id`, `nces_name` | Matching school in the NCES directory (CCD public / PSS private), when confirmed |
| `nces_match` | `matched`, `review` (a likely match that needs a person to confirm) or `none` |

## Branches

- `logos/main` holds the merged states.
- Each state arrives on its own branch, `logos/<st>`, with its own pull request.

## Hudl packages

To turn a state into the `HS Logos <ST>.zip` delivery, keyed by `hudl_id` with its
`logo-index.csv` (statuses `found`, `not found`, `ambiguous`, `unavailable`), run:

```bash
pip install -r tools/requirements.txt
python tools/package_logos.py --schools hudl_schools.csv --states IL
```

A logo is used only when the school's city, and its mascot where both sides list one,
confirm the match. Anything uncertain is marked `ambiguous`, never guessed.
