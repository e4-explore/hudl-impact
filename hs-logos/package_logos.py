#!/usr/bin/env python3
"""Package one logo per Hudl school into per-state zips.

Input: the Hudl school CSV (hudl_id, state, full_name, short_name, abbreviation,
mascot, city, zip, country, hudl_has_image, hidden) plus the collected logos in
output/manifests/<ST>.csv.

For each visible school:
  * candidates are logo schools in the same state with a close name
    (full_name or short_name);
  * a logo is taken only when the city matches, and the mascot too when both
    sides list one; if more than one school passes, or the name fits but the
    city or mascot doesn't, the row is "ambiguous";
  * the primary athletic mark (the school page's first logo) is copied, unedited,
    to <STATE>/<hudl_id>.<ext>.

Output in output/packages/:
  HS Logos <ST>.zip   (or "HS Logos <ST> 01.zip", 02 ... when over 25 MB),
  each with the <ST>/ folder and its own logo-index.csv at the top level, plus
  logo-index <ST>.csv covering every row of the state.

    python hs-logos/package_logos.py --schools hudl_schools.csv --states IL
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import zipfile

import pandas as pd
from PIL import Image
from rapidfuzz import fuzz

import manifests
from match_schools import name_score
from names import ascii_fold, normalize_city, normalize_name

log = logging.getLogger("package")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output")

INDEX_FIELDS = ["hudl_id", "state", "school", "filename", "source", "status", "notes"]
MAX_ZIP_BYTES = 24_500_000   # stay safely under 25 MB including the index
NAME_STRONG = 88.0           # a name this close is a candidate on its own
NAME_WITH_MASCOT = 75.0      # a looser name counts when city and mascot both agree
MIN_SIDE = 128


def norm_mascot(m: str) -> str:
    s = re.sub(r"[^a-z ]+", " ", ascii_fold(m or "").lower())
    words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in s.split()]
    return " ".join(w for w in words if w not in {"the", "lady", "fighting"})


def mascot_match(a: str, b: str) -> str:
    a, b = norm_mascot(a), norm_mascot(b)
    if not a or not b:
        return "unknown"
    if a == b or fuzz.ratio(a, b) >= 85 or fuzz.token_set_ratio(a, b) == 100:
        return "yes"
    return "no"


def load_logo_schools(out_dir: str, states: list[str] | None) -> pd.DataFrame:
    m = manifests.read_all(out_dir, states)
    m = m[m["logo_index"] == "1"].copy()   # primary athletic mark only
    m["norm"] = m["school_name"].map(normalize_name)
    m["norm_city"] = m["city"].map(normalize_city)
    return m.reset_index(drop=True)


def decide(row: dict, pool: pd.DataFrame) -> tuple[str, pd.Series | None, str]:
    """Return (status, logo_row, note) for one Hudl school."""
    names = {normalize_name(row.get("full_name", "")), normalize_name(row.get("short_name", ""))} - {""}
    if not names or pool.empty:
        return "not found", None, ""
    city = normalize_city(row.get("city", ""))
    scored = []
    for _, lg in pool.iterrows():
        s = max(name_score(n, lg["norm"]) for n in names)
        if s < NAME_WITH_MASCOT:
            continue
        cm = "unknown" if not city or not lg["norm_city"] else ("yes" if city == lg["norm_city"] else "no")
        mm = mascot_match(row.get("mascot", ""), lg["mascot"])
        scored.append((s, cm, mm, lg))
    confirmed = [x for x in scored if x[1] == "yes" and x[2] != "no"
                 and (x[0] >= NAME_STRONG or x[2] == "yes")]
    if len(confirmed) == 1:
        return "found", confirmed[0][3], ""
    if len(confirmed) > 1:
        # Prefer a unique mascot agreement before giving up.
        with_mascot = [x for x in confirmed if x[2] == "yes"]
        if len(with_mascot) == 1:
            return "found", with_mascot[0][3], ""
        return "ambiguous", None, f"{len(confirmed)} schools with this name in {row.get('city', '')}"
    near = sorted((x for x in scored if x[0] >= NAME_STRONG), key=lambda x: -x[0])
    if near:
        s, cm, mm, lg = near[0]
        if cm == "no":
            return "ambiguous", None, f"Name matches a school in {lg['city']}, not {row.get('city', '')}"
        if mm == "no":
            return "ambiguous", None, f"Name and city match but mascot is {lg['mascot']}"
        if cm == "unknown":
            return "ambiguous", None, "Name matches but the city can't be confirmed"
    return "not found", None, ""


def check_image(path: str) -> tuple[bool, str]:
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return False, "Image file won't open"
    return True, (f"Largest available is {w}x{h}" if min(w, h) < MIN_SIDE else "")


def write_zip(path: str, st: str, rows: list[dict], logo_root: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for r in rows:
            if r["status"] == "found":
                zf.write(os.path.join(logo_root, r["_local"]), r["filename"], compress_type=zipfile.ZIP_STORED)
        zf.writestr(zipfile.ZipInfo("logo-index.csv", date_time=(2026, 1, 1, 0, 0, 0)),
                    index_csv(rows), compress_type=zipfile.ZIP_DEFLATED)


def index_csv(rows: list[dict]) -> str:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=INDEX_FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def package_state(st: str, rows: list[dict], out_dir: str, logo_root: str) -> list[str]:
    found = [r for r in rows if r["status"] == "found"]
    other = [r for r in rows if r["status"] != "found"]
    parts: list[list[dict]] = [[]]
    size = 0
    for r in found:
        fsize = os.path.getsize(os.path.join(logo_root, r["_local"]))
        if parts[-1] and size + fsize > MAX_ZIP_BYTES:
            parts.append([])
            size = 0
        parts[-1].append(r)
        size += fsize
    parts[0] = parts[0] + other  # rows without a file go in the first part's index
    names = []
    for i, part in enumerate(parts, 1):
        part.sort(key=lambda r: (r["status"] != "found", r["school"].lower()))
        name = f"HS Logos {st}.zip" if len(parts) == 1 else f"HS Logos {st} {i:02d}.zip"
        write_zip(os.path.join(out_dir, name), st, part, logo_root)
        names.append(name)
    with open(os.path.join(out_dir, f"logo-index {st}.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(index_csv(sorted(rows, key=lambda r: (r["status"] != "found", r["school"].lower()))))
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schools", required=True, help="Hudl school CSV")
    ap.add_argument("--states", nargs="*", help="Only these states (default: every state in the CSV)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-source", action="store_true", help="Leave the index's source column empty")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    schools = pd.read_csv(args.schools, dtype=str, keep_default_na=False)
    schools.columns = [c.strip().lower() for c in schools.columns]
    schools["state"] = schools["state"].str.strip().str.upper()
    visible = schools[schools["hidden"].str.strip().str.lower() != "yes"]
    if args.states:
        visible = visible[visible["state"].isin({s.upper() for s in args.states})]
    log.info("%d schools (%d hidden skipped)", len(visible), (schools["hidden"].str.strip().str.lower() == "yes").sum())

    logos = load_logo_schools(args.out, args.states)
    pkg_dir = os.path.join(args.out, "packages")
    os.makedirs(pkg_dir, exist_ok=True)

    for st, group in visible.groupby("state"):
        pool = logos[logos["state"] == st]
        rows = []
        for rec in group.to_dict("records"):
            status, lg, note = decide(rec, pool)
            row = {"hudl_id": rec["hudl_id"], "state": st, "school": rec.get("full_name") or rec.get("short_name", ""),
                   "filename": "", "source": "", "status": status, "notes": note}
            if lg is not None:
                ok, img_note = check_image(os.path.join(args.out, lg["local_path"]))
                row["source"] = "" if args.no_source else lg["school_page_url"]
                if ok:
                    ext = os.path.splitext(lg["local_path"])[1].lower()
                    row["filename"] = f"{st}/{rec['hudl_id']}{ext}"
                    row["_local"] = lg["local_path"]
                    row["notes"] = img_note
                else:
                    row["status"], row["notes"] = "unavailable", img_note
            rows.append(row)
        zips = package_state(st, rows, pkg_dir, args.out)
        counts = pd.Series([r["status"] for r in rows]).value_counts().to_dict()
        log.info("%s: %d found, %d not found, %d ambiguous, %d unavailable -> %s", st,
                 counts.get("found", 0), counts.get("not found", 0), counts.get("ambiguous", 0),
                 counts.get("unavailable", 0), ", ".join(zips))
    return 0


if __name__ == "__main__":
    sys.exit(main())
