#!/usr/bin/env python3
"""Part 3: cross-reference hslogos.com schools against the NCES high-school list.

Matching: same state, then rapidfuzz on normalised names (see names.py). When
several NCES schools score about the same, the hslogos page's town breaks the tie.

Outputs (in output/results/):
  matched.csv            NCES record + logo paths + match confidence (high confidence only)
  needs_review.csv       hslogos schools whose best match is low-confidence, ambiguous or absent
  missing_logos.csv      NCES high schools with no matched logo, sorted by state
  coverage_by_state.csv  total schools, schools with logos, percentage

    python scraper/crossref.py                 # every state
    python scraper/crossref.py --states IL     # limit to some states
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import pandas as pd
from rapidfuzz import fuzz, process

from names import normalize_city, normalize_name

log = logging.getLogger("crossref")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output")

HIGH_CONFIDENCE = 90.0  # >= this -> matched.csv
REVIEW_FLOOR = 60.0     # below this the best candidate is reported as "no_match"
TIE_MARGIN = 5.0        # candidates within this many points of the best are "tied"
CITY_BONUS = 5.0
CITY_PENALTY = 10.0
N_CANDIDATES = 3

NCES_COLS = ["nces_id", "name", "city", "state", "zip", "school_type", "grade_span", "source", "district"]


def name_score(a: str, b: str, **_) -> float:
    """Blend of token-sort (penalises extra words) and token-set (tolerates them)."""
    return 0.5 * fuzz.token_sort_ratio(a, b) + 0.5 * fuzz.token_set_ratio(a, b)


def load_logo_schools(manifest_path: str) -> pd.DataFrame:
    m = pd.read_csv(manifest_path, dtype=str, keep_default_na=False)
    m["logo_index"] = m["logo_index"].astype(int)
    m = m.sort_values(["school_page_url", "logo_index"])
    g = m.groupby("school_page_url", sort=False)
    schools = g.agg(
        state=("state", "first"),
        school_name=("school_name", "first"),
        mascot=("mascot", "first"),
        city=("city", "first"),
        logo_count=("local_path", "size"),
        logo_paths=("local_path", lambda s: "; ".join(s)),
    ).reset_index()
    return schools


def match_state(logos: pd.DataFrame, nces: pd.DataFrame) -> list[dict]:
    choices = nces["norm"].tolist()
    results = []
    for row in logos.itertuples(index=False):
        query = normalize_name(row.school_name)
        city = normalize_city(row.city)
        hits = process.extract(query, choices, scorer=name_score, limit=15) if choices else []
        cands = []
        for _, score, idx in hits:
            rec = nces.iloc[idx]
            city_match = "unknown" if not city else ("yes" if city == rec["norm_city"] else "no")
            conf = score + (CITY_BONUS if city_match == "yes" else 0) - (CITY_PENALTY if city_match == "no" else 0)
            cands.append({"idx": idx, "name_score": round(score, 1), "city_match": city_match,
                          "confidence": round(max(0.0, min(100.0, conf)), 1)})
        reason = ""
        best = None
        if cands:
            top = cands[0]["name_score"]
            tied = [c for c in cands if c["name_score"] >= top - TIE_MARGIN]
            # Tie-break: prefer a city match, then the highest name score.
            tied.sort(key=lambda c: (c["city_match"] == "yes", c["name_score"]), reverse=True)
            best = tied[0]
            rivals = [c for c in tied[1:] if c["city_match"] == best["city_match"]
                      and c["name_score"] >= best["name_score"] - 1.0]
            if rivals and best["name_score"] >= REVIEW_FLOOR:
                reason = "ambiguous"
            # Re-rank the remaining candidates for the review file.
            others = sorted((c for c in cands if c is not best), key=lambda c: c["confidence"], reverse=True)
            cands = [best] + others
        if best is None or best["confidence"] < REVIEW_FLOOR:
            reason = "no_match"
        elif not reason and best["confidence"] < HIGH_CONFIDENCE:
            reason = "low_confidence"
        results.append({"logo": row._asdict(), "best": best, "cands": cands[:N_CANDIDATES], "reason": reason})
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--states", nargs="*", help="Limit to these states (default: all NCES states)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    nces = pd.read_csv(os.path.join(args.out, "nces", "nces_high_schools.csv"), dtype=str, keep_default_na=False)
    logos = load_logo_schools(os.path.join(args.out, "manifest.csv"))
    if args.states:
        wanted = {s.upper() for s in args.states}
        nces = nces[nces["state"].isin(wanted)]
        logos = logos[logos["state"].isin(wanted)]
    nces = nces.reset_index(drop=True)
    nces["norm"] = nces["name"].map(normalize_name)
    nces["norm_city"] = nces["city"].map(normalize_city)

    matched_rows, review_rows = [], []
    claimed: dict[str, dict] = {}  # nces_id -> best matched row so far
    for st, lg in logos.groupby("state"):
        ns = nces[nces["state"] == st].reset_index(drop=True)
        for res in match_state(lg, ns):
            logo, best = res["logo"], res["best"]
            base = {
                "hslogos_school_name": logo["school_name"], "hslogos_city": logo["city"],
                "mascot": logo["mascot"], "school_page_url": logo["school_page_url"],
                "logo_count": logo["logo_count"], "logo_paths": logo["logo_paths"],
            }
            if not res["reason"]:
                rec = ns.iloc[best["idx"]]
                row = {**{c: rec[c] for c in NCES_COLS}, **base,
                       "match_confidence": best["confidence"], "name_score": best["name_score"],
                       "city_match": best["city_match"]}
                prev = claimed.get(rec["nces_id"])
                if prev is None or row["match_confidence"] > prev["match_confidence"]:
                    if prev is not None:
                        review_rows.append(_review_row(st, prev_base(prev), "duplicate_target", [], ns,
                                                       dup_of=row, own_conf=prev["match_confidence"]))
                        matched_rows.remove(prev)
                    claimed[rec["nces_id"]] = row
                    matched_rows.append(row)
                else:
                    review_rows.append(_review_row(st, base, "duplicate_target", res["cands"], ns,
                                                   dup_of=prev))
                continue
            review_rows.append(_review_row(st, base, res["reason"], res["cands"], ns))

    results_dir = os.path.join(args.out, "results")
    os.makedirs(results_dir, exist_ok=True)

    matched = pd.DataFrame(matched_rows)
    if matched.empty:
        matched = pd.DataFrame(columns=NCES_COLS + ["match_confidence"])
    matched = matched.sort_values(["state", "city", "name"])
    matched.to_csv(os.path.join(results_dir, "matched.csv"), index=False)

    review = pd.DataFrame(review_rows)
    if not review.empty:
        review = review.sort_values(["state", "reason", "hslogos_school_name"])
    review.to_csv(os.path.join(results_dir, "needs_review.csv"), index=False)

    have = set(matched["nces_id"])
    possible: dict[str, str] = {}
    for r in review_rows:
        if r["reason"] != "no_match" and r.get("candidate1_nces_id"):
            possible.setdefault(r["candidate1_nces_id"], r["hslogos_school_name"])
    missing = nces[~nces["nces_id"].isin(have)][NCES_COLS].copy()
    missing["possible_hslogos_match"] = missing["nces_id"].map(possible).fillna("")
    missing = missing.sort_values(["state", "city", "name"])
    missing.to_csv(os.path.join(results_dir, "missing_logos.csv"), index=False)

    cov = coverage(nces, matched, logos, review)
    cov.to_csv(os.path.join(results_dir, "coverage_by_state.csv"), index=False)

    total = cov.iloc[-1]
    log.info("hslogos schools: %d | matched: %d | needs review: %d", len(logos), len(matched), len(review))
    log.info("NCES high schools: %d | with logos: %d (%.1f%%) | core schools: %d with logos (%.1f%%)",
             total["nces_high_schools"], total["schools_with_logos"], total["pct_with_logos"],
             total["core_schools_with_logos"], total["pct_core_with_logos"])
    log.info("Results in %s", results_dir)
    return 0


def prev_base(row: dict) -> dict:
    keys = ["hslogos_school_name", "hslogos_city", "mascot", "school_page_url", "logo_count", "logo_paths"]
    return {k: row[k] for k in keys}


def _review_row(st: str, base: dict, reason: str, cands: list[dict], ns: pd.DataFrame,
                dup_of: dict | None = None, own_conf: float | None = None) -> dict:
    row = {"state": st, **base, "reason": reason,
           "best_confidence": cands[0]["confidence"] if cands else own_conf}
    if dup_of is not None:
        row["note"] = f"NCES {dup_of['nces_id']} ({dup_of['name']}) also matched by {dup_of['hslogos_school_name']}"
        if not cands:
            row.update({"candidate1_nces_id": dup_of["nces_id"], "candidate1_name": dup_of["name"],
                        "candidate1_city": dup_of["city"], "candidate1_confidence": own_conf})
    for i, c in enumerate(cands, 1):
        rec = ns.iloc[c["idx"]]
        row[f"candidate{i}_nces_id"] = rec["nces_id"]
        row[f"candidate{i}_name"] = rec["name"]
        row[f"candidate{i}_city"] = rec["city"]
        row[f"candidate{i}_type"] = rec["school_type"]
        row[f"candidate{i}_confidence"] = c["confidence"]
        row[f"candidate{i}_city_match"] = c["city_match"]
    return row


def coverage(nces: pd.DataFrame, matched: pd.DataFrame, logos: pd.DataFrame, review: pd.DataFrame) -> pd.DataFrame:
    n = nces.copy()
    n["has_logo"] = n["nces_id"].isin(set(matched["nces_id"]))
    # "Core" = regular public (incl. charter) + private: excludes alternative,
    # special-education and CTE centres, which rarely have athletics or logos.
    n["core"] = n["school_type"].str.startswith("Public - Regular") | (n["school_type"] == "Private")
    g = n.groupby("state")
    cov = pd.DataFrame({
        "nces_high_schools": g.size(),
        "schools_with_logos": g["has_logo"].sum(),
        "core_schools": g["core"].sum(),
        "core_schools_with_logos": g.apply(lambda d: (d["has_logo"] & d["core"]).sum(), include_groups=False),
    })
    cov["hslogos_schools"] = logos.groupby("state").size().reindex(cov.index).fillna(0).astype(int)
    cov["hslogos_in_review"] = (review.groupby("state").size().reindex(cov.index).fillna(0).astype(int)
                                if not review.empty else 0)
    cov = cov.reset_index()
    tot = cov.drop(columns="state").sum()
    tot["state"] = "TOTAL"
    cov = pd.concat([cov, tot.to_frame().T], ignore_index=True)
    for col in cov.columns.drop("state"):
        cov[col] = cov[col].astype(int)
    cov["pct_with_logos"] = (100 * cov["schools_with_logos"] / cov["nces_high_schools"].where(cov["nces_high_schools"] > 0)).round(1).fillna(0)
    cov["pct_core_with_logos"] = (100 * cov["core_schools_with_logos"] / cov["core_schools"].where(cov["core_schools"] > 0)).round(1).fillna(0)
    return cov[["state", "nces_high_schools", "schools_with_logos", "pct_with_logos", "core_schools",
                "core_schools_with_logos", "pct_core_with_logos", "hslogos_schools", "hslogos_in_review"]]


if __name__ == "__main__":
    sys.exit(main())
