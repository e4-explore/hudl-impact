#!/usr/bin/env python3
"""Zip each collected state's primary logos for hand-off.

    python hs-logos/bundle_states.py --dest ~/handoff            # every collected state
    python hs-logos/bundle_states.py --dest ~/handoff --states IL OH

Each state becomes "HS Logos <ST>.zip" holding logos/<ST>/<school>.<ext>
(unedited) and logos/<ST>/logo-index.csv. A state over 25 MB is split into
"HS Logos <ST> 01.zip", "02", ...; every part carries the state's full index, so
unzipping all the zips into one folder gives a complete logos/ tree.
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import os
import sys
import tempfile
import zipfile

import export_state

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output")
MAX_ZIP_BYTES = 24_500_000


def bundle(st: str, out_dir: str, dest: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        export_state.main(["--state", st, "--dest", tmp, "--out", out_dir])
        with open(os.path.join(tmp, st, "logo-index.csv"), newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        fields = list(rows[0].keys()) if rows else export_state.FIELDS
        parts: list[list[dict]] = [[]]
        size = 0
        for r in rows:
            fsize = os.path.getsize(os.path.join(tmp, r["filename"]))
            if parts[-1] and size + fsize > MAX_ZIP_BYTES:
                parts.append([])
                size = 0
            parts[-1].append(r)
            size += fsize
        for old in glob.glob(os.path.join(dest, f"HS Logos {st}*.zip")):
            os.remove(old)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        names = []
        for i, part in enumerate(parts, 1):
            name = f"HS Logos {st}.zip" if len(parts) == 1 else f"HS Logos {st} {i:02d}.zip"
            with zipfile.ZipFile(os.path.join(dest, name), "w") as zf:
                for r in part:
                    zf.write(os.path.join(tmp, r["filename"]), f"logos/{r['filename']}", compress_type=zipfile.ZIP_STORED)
                zf.writestr(f"logos/{st}/logo-index.csv", buf.getvalue(), compress_type=zipfile.ZIP_DEFLATED)
            names.append(name)
        return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--states", nargs="*")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    os.makedirs(args.dest, exist_ok=True)
    states = [s.upper() for s in args.states] if args.states else sorted(
        os.path.basename(p)[:-4] for p in glob.glob(os.path.join(args.out, "manifests", "*.csv")))
    for st in states:
        names = bundle(st, args.out, args.dest)
        print(f"{st}: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
