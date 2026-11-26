#!/usr/bin/env python3
"""Part 2: build the master list of US high schools from NCES.

Sources (latest release found at run time, both from nces.ed.gov):
  * Common Core of Data (CCD) public school directory  (file "029")
  * Private School Universe Survey (PSS) public-use CSV

A school counts as a high school if it offers any of grades 9-12.
Output: output/nces/nces_high_schools.csv with
  nces_id, name, city, state, zip, school_type, grade_span, source, (+ a few extras)

    python scraper/build_nces.py
    python scraper/build_nces.py --ccd-url <zip> --pss-url <zip>   # pin specific files
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import zipfile

import pandas as pd

from http_client import PoliteSession

log = logging.getLogger("nces")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "output")

CCD_API = "https://nces.ed.gov/ccd/datatables/api/"
PSS_PAGE = "https://nces.ed.gov/surveys/pss/pssdata.asp"
PSS_ZIP_BASE = "https://nces.ed.gov/surveys/pss/"

# 50 states + DC. Territories (PR, GU, VI, AS, MP) are dropped unless --include-territories.
STATES = set("""AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT
NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY""".split())

CCD_ACTIVE = {"Open", "New", "Added", "Reopened", "Changed Boundary/Agency"}

# PSS LOGR/HIGR codes (see PSS codebook): 1=ungraded, 2=PK, 3=K, 4=TK, 5=T1,
# 6=grade 1 ... 17=grade 12.
PSS_GRADE_LABEL = {1: "UG", 2: "PK", 3: "KG", 4: "TK", 5: "T1"}
PSS_GRADE_LABEL.update({c: f"{c - 5:02d}" for c in range(6, 18)})

OUT_FIELDS = ["nces_id", "name", "city", "state", "zip", "school_type", "grade_span", "source",
              "district", "charter", "level"]


# --------------------------------------------------------------------------- discovery
def latest_ccd_url(session: PoliteSession) -> str:
    years = session.get(CCD_API + "Lookup/").json()["schoolYears"]
    years = sorted((y for y in years if y["id"]), key=lambda y: y["schoolYearShort"] or "", reverse=True)
    for y in years:
        data = session.get(f"{CCD_API}File/0/0/{y['id']}/0/0/0").json()
        urls = re.findall(r'https?://[^"]*ccd_sch_029_[^"]*_w_[^"]*\.zip', json.dumps(data))
        if urls:
            # Highest version letter/number within the year wins.
            return sorted(set(urls))[-1]
    raise RuntimeError("No CCD school directory (029) file found via the NCES files API")


def latest_pss_url(session: PoliteSession) -> str:
    html = session.get(PSS_PAGE).text
    files = sorted(set(re.findall(r"zip/pss(\d{4})_pu_csv\.zip", html)), key=lambda yy: int(yy[:2]))
    if not files:
        raise RuntimeError("No PSS CSV file found on " + PSS_PAGE)
    return f"{PSS_ZIP_BASE}zip/pss{files[-1]}_pu_csv.zip"


def fetch_zip(session: PoliteSession, url: str, raw_dir: str) -> str:
    dest = os.path.join(raw_dir, url.rsplit("/", 1)[1])
    if os.path.exists(dest):
        log.info("Using cached %s", dest)
    else:
        log.info("Downloading %s", url)
        session.download(url, dest)
    return dest


def read_csv_from_zip(path: str, usecols=None) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError(f"No CSV inside {path}")
        with zf.open(names[0]) as fh:
            raw = fh.read()
    return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="latin-1", keep_default_na=False,
                       usecols=usecols)


# --------------------------------------------------------------------------- transforms
def ccd_high_schools(df: pd.DataFrame) -> pd.DataFrame:
    grade_cols = [f"G_{g}_OFFERED" for g in (9, 10, 11, 12)]
    offers_hs = (df[grade_cols] == "Yes").any(axis=1)
    active = df["SY_STATUS_TEXT"].isin(CCD_ACTIVE)
    hs = df[offers_hs & active].copy()
    state = hs["LSTATE"].where(hs["LSTATE"] != "", hs["ST"])
    charter = hs["CHARTER_TEXT"].eq("Yes")
    return pd.DataFrame({
        "nces_id": hs["NCESSCH"],
        "name": hs["SCH_NAME"].str.strip(),
        "city": hs["LCITY"].str.strip().str.title(),
        "state": state,
        "zip": hs["LZIP"].str.zfill(5),
        "school_type": "Public - " + hs["SCH_TYPE_TEXT"] + charter.map({True: " (Charter)", False: ""}),
        "grade_span": hs["GSLO"] + "-" + hs["GSHI"],
        "source": "CCD " + hs["SCHOOL_YEAR"],
        "district": hs["LEA_NAME"].str.strip(),
        "charter": hs["CHARTER_TEXT"],
        "level": hs["LEVEL"],
    })


def pss_high_schools(df: pd.DataFrame, year_label: str) -> pd.DataFrame:
    lo_col = next(c for c in df.columns if re.fullmatch(r"LOGR\d{4}", c))
    hi_col = next(c for c in df.columns if re.fullmatch(r"HIGR\d{4}", c))
    lo = pd.to_numeric(df[lo_col], errors="coerce")
    hi = pd.to_numeric(df[hi_col], errors="coerce")
    # Serves grades 9-12 if its range [lo, hi] overlaps codes 14..17 (ungraded=1 excluded).
    offers_hs = (hi >= 14) & (lo <= 17) & (lo >= 2)
    hs = df[offers_hs].copy()
    lo, hi = lo[offers_hs].astype(int), hi[offers_hs].astype(int)
    city = hs["PL_CIT"].where(hs["PL_CIT"].str.strip() != "", hs["PCITY"])
    state = hs["PL_STABB"].where(hs["PL_STABB"].str.strip() != "", hs["PSTABB"])
    zip_ = hs["PL_ZIP"].where(hs["PL_ZIP"].str.strip() != "", hs["PZIP"])
    level = hs["LEVEL"].map({"1": "Elementary", "2": "Secondary", "3": "Combined"}).fillna("")
    return pd.DataFrame({
        "nces_id": hs["PPIN"],
        "name": hs["PINST"].str.strip().str.title(),
        "city": city.str.strip().str.title(),
        "state": state.str.strip(),
        "zip": zip_.str.strip().str.zfill(5),
        "school_type": "Private",
        "grade_span": lo.map(PSS_GRADE_LABEL) + "-" + hi.map(PSS_GRADE_LABEL),
        "source": "PSS " + year_label,
        "district": "",
        "charter": "",
        "level": level,
    })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--ccd-url", help="Pin a specific CCD directory zip (default: latest)")
    ap.add_argument("--pss-url", help="Pin a specific PSS CSV zip (default: latest)")
    ap.add_argument("--include-territories", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    raw_dir = os.path.join(args.out, "nces", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    # NCES is a government data portal; a shorter delay between the few calls is fine.
    session = PoliteSession(min_delay=0.5, max_delay=1.0)

    ccd_url = args.ccd_url or latest_ccd_url(session)
    pss_url = args.pss_url or latest_pss_url(session)
    log.info("CCD: %s", ccd_url)
    log.info("PSS: %s", pss_url)

    ccd_cols = ["SCHOOL_YEAR", "ST", "SCH_NAME", "LEA_NAME", "NCESSCH", "LCITY", "LSTATE", "LZIP",
                "SY_STATUS_TEXT", "SCH_TYPE_TEXT", "CHARTER_TEXT", "GSLO", "GSHI", "LEVEL",
                "G_9_OFFERED", "G_10_OFFERED", "G_11_OFFERED", "G_12_OFFERED"]
    ccd = ccd_high_schools(read_csv_from_zip(fetch_zip(session, ccd_url, raw_dir), usecols=ccd_cols))

    pss_year = re.search(r"pss(\d{2})(\d{2})", pss_url)
    pss_label = f"20{pss_year.group(1)}-20{pss_year.group(2)}" if pss_year else ""
    pss_raw = read_csv_from_zip(fetch_zip(session, pss_url, raw_dir))
    pss = pss_high_schools(pss_raw, pss_label)

    allhs = pd.concat([ccd, pss], ignore_index=True)
    if not args.include_territories:
        allhs = allhs[allhs["state"].isin(STATES)]
    allhs = allhs.sort_values(["state", "city", "name"]).reset_index(drop=True)

    out = os.path.join(args.out, "nces", "nces_high_schools.csv")
    allhs[OUT_FIELDS].to_csv(out, index=False)
    log.info("Public high schools (CCD): %d", (allhs["school_type"] != "Private").sum())
    log.info("Private high schools (PSS): %d", (allhs["school_type"] == "Private").sum())
    log.info("Wrote %d rows to %s", len(allhs), out)
    with open(os.path.join(args.out, "nces", "sources.json"), "w") as fh:
        json.dump({"ccd_url": ccd_url, "pss_url": pss_url}, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
