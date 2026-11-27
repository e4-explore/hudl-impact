import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from names import normalize_city, normalize_name  # noqa: E402
from package_logos import decide, mascot_match  # noqa: E402


def pool(*schools):
    df = pd.DataFrame([dict(school_name=n, city=c, mascot=m, local_path=f"logos/IL/{i}.png",
                            school_page_url=f"https://example.test/{i}.html")
                       for i, (n, c, m) in enumerate(schools)])
    df["norm"] = df["school_name"].map(normalize_name)
    df["norm_city"] = df["city"].map(normalize_city)
    return df


def row(name, city, mascot, short=""):
    return {"full_name": name, "short_name": short, "city": city, "mascot": mascot}


P = pool(("Lincoln High School", "Lincoln", "Railsplitters"),
         ("Central High School", "Clifton", "Comets"),
         ("Champaign Central High School", "Champaign", "Maroons"),
         ("Twin Academy", "Peoria", "Owls"),
         ("Twin Academy", "Peoria", "Owls"))


def test_found_needs_city():
    status, lg, _ = decide(row("Lincoln Community High School", "Lincoln", "Railsplitters"), P)
    assert status == "found" and lg["school_name"] == "Lincoln High School"


def test_wrong_city_is_ambiguous_not_guessed():
    status, lg, note = decide(row("Lincoln High School", "Springfield", "Senators"), P)
    assert status == "ambiguous" and lg is None and "Lincoln" in note


def test_mascot_conflict_is_ambiguous():
    status, _, note = decide(row("Lincoln High School", "Lincoln", "Bulldogs"), P)
    assert status == "ambiguous" and "mascot" in note


def test_city_and_mascot_confirm_a_looser_name():
    status, lg, _ = decide(row("Central High School", "Champaign", "Maroons"), P)
    assert status == "found" and lg["city"] == "Champaign"


def test_two_identical_schools_are_ambiguous():
    assert decide(row("Twin Academy", "Peoria", "Owls"), P)[0] == "ambiguous"


def test_not_found():
    assert decide(row("Nowhere Prep", "Peoria", "Owls"), P)[0] == "not found"


def test_mascot_plurals():
    assert mascot_match("Knights", "Knight") == "yes"
    assert mascot_match("Fighting Illini", "Illini") == "yes"
    assert mascot_match("Tigers", "") == "unknown"
