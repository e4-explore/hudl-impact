#!/usr/bin/env python3
"""Re-download logos listed in the per-state manifests, verifying each checksum.

Use this to rebuild the logo files on another machine from the committed
output/manifests/<ST>.csv files, without crawling the site again.

    python hs-logos/fetch_from_manifest.py                    # every state, primary logos
    python hs-logos/fetch_from_manifest.py --states IL OH
    python hs-logos/fetch_from_manifest.py --all-logos        # alternates too

Files land at output/<local_path> (output/logos/<ST>/<school>/<file>). Files already
present with the right sha256 are skipped, so it can be stopped and rerun. Any file
whose checksum differs is reported in output/fetch_mismatches.csv.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import sys

import manifests
from http_client import PoliteSession

log = logging.getLogger("fetch")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--states", nargs="*")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--all-logos", action="store_true", help="Also fetch alternate logos")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    m = manifests.read_all(args.out, args.states)
    if not args.all_logos:
        m = m[m["logo_index"] == "1"]
    session = PoliteSession()
    done = skipped = 0
    bad: list[tuple[str, str, str]] = []
    for i, r in enumerate(m.itertuples(), 1):
        dest = os.path.join(args.out, r.local_path)
        if os.path.exists(dest) and sha256(dest) == r.sha256:
            skipped += 1
            continue
        try:
            session.download(r.image_url, dest)
        except Exception as exc:
            bad.append((r.image_url, r.local_path, f"download failed: {exc}"))
            continue
        got = sha256(dest)
        if got != r.sha256:
            bad.append((r.image_url, r.local_path, f"sha256 {got} != {r.sha256}"))
        done += 1
        if i % 50 == 0 or i == len(m):
            log.info("%d/%d checked, %d downloaded, %d already present, %d problems", i, len(m), done, skipped, len(bad))
    if bad:
        with open(os.path.join(args.out, "fetch_mismatches.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["image_url", "local_path", "problem"])
            w.writerows(bad)
        log.warning("%d problems written to fetch_mismatches.csv", len(bad))
    log.info("Done: %d downloaded, %d already present, %d problems", done, skipped, len(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
