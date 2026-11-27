"""Per-state logo manifests: output/manifests/<ST>.csv, one file per state so each
state can be collected, reviewed and committed on its own."""

from __future__ import annotations

import glob
import os

import pandas as pd


def manifest_path(out_dir: str, state: str) -> str:
    return os.path.join(out_dir, "manifests", f"{state.upper()}.csv")


def read_all(out_dir: str, states: list[str] | None = None) -> pd.DataFrame:
    paths = sorted(glob.glob(os.path.join(out_dir, "manifests", "*.csv")))
    if states:
        wanted = {s.upper() for s in states}
        paths = [p for p in paths if os.path.basename(p)[:-4] in wanted]
    if not paths:
        raise SystemExit(f"No manifests in {os.path.join(out_dir, 'manifests')}; run collect_logos.py first")
    return pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False) for p in paths], ignore_index=True)
