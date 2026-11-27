#!/usr/bin/env python3
"""Export one state's primary logos into a clean data folder.

    python hs-logos/export_state.py --state IL --dest /path/to/logos-repo

Writes
  <dest>/<ST>/<school>.<ext>       primary athletic mark, unedited
  <dest>/<ST>/logo-index.csv       one row per school

Index columns: state, school, mascot, city, county, filename, width, height,
sha256, nces_id, nces_name, nces_match (matched | review | none).
The NCES columns come from output/results/matched.csv and needs_review.csv, so
run match_schools.py for the state first.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys

import pandas as pd

import manifests

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output")
FIELDS = ["state", "school", "mascot", "city", "county", "filename", "width", "height", "sha256",
          "nces_id", "nces_name", "nces_match"]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "school"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    st = args.state.upper()

    m = manifests.read_all(args.out, [st])
    m = m[m["logo_index"] == "1"].sort_values("school_name", key=lambda s: s.str.lower())

    res = os.path.join(args.out, "results")
    matched, review = {}, set()
    if os.path.exists(os.path.join(res, "matched.csv")):
        mt = pd.read_csv(os.path.join(res, "matched.csv"), dtype=str, keep_default_na=False)
        matched = {r.school_page_url: r for r in mt.itertuples()}
    if os.path.exists(os.path.join(res, "needs_review.csv")):
        rv = pd.read_csv(os.path.join(res, "needs_review.csv"), dtype=str, keep_default_na=False)
        review = set(rv.get("school_page_url", []))

    state_dir = os.path.join(args.dest, st)
    if os.path.isdir(state_dir):
        shutil.rmtree(state_dir)
    os.makedirs(state_dir)
    used: set[str] = set()
    rows = []
    for r in m.itertuples():
        base = slugify(r.school_name)
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        ext = os.path.splitext(r.local_path)[1].lower()
        filename = f"{slug}{ext}"
        shutil.copy2(os.path.join(args.out, r.local_path), os.path.join(state_dir, filename))
        hit = matched.get(r.school_page_url)
        rows.append({
            "state": st, "school": r.school_name, "mascot": r.mascot, "city": r.city, "county": r.county,
            "filename": f"{st}/{filename}", "width": r.image_width, "height": r.image_height, "sha256": r.sha256,
            "nces_id": hit.nces_id if hit else "", "nces_name": hit.name if hit else "",
            "nces_match": "matched" if hit else ("review" if r.school_page_url in review else "none"),
        })
    with open(os.path.join(state_dir, "logo-index.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    counts = pd.Series([r["nces_match"] for r in rows]).value_counts().to_dict()
    print(f"{st}: {len(rows)} logos exported to {state_dir} ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
