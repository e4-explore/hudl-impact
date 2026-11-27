import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from names import normalize_city, normalize_name  # noqa: E402
from collect_logos import normalize_url  # noqa: E402


def test_high_school_variants_collapse():
    assert normalize_name("Lincoln High School") == normalize_name("Lincoln HS") == "lincoln"
    assert normalize_name("Lincoln Senior High School") == "lincoln"
    assert normalize_name("Red Hill Jr/Sr High School") == "red hill"
    assert normalize_name("Lincoln-Way Community H S") == "lincoln way"


def test_saint_and_abbreviations():
    assert normalize_name("St. Ignatius College Prep") == normalize_name("Saint Ignatius College Preparatory")
    assert normalize_name("Mt. Carmel High School") == "mount carmel"
    assert normalize_name("N. Chicago Community HS") == "north chicago"
    # A middle initial is not a compass direction.
    assert normalize_name("Adlai E. Stevenson High School") == "adlai e stevenson"


def test_generic_name_not_emptied():
    assert normalize_name("Community High School") == "community"


def test_city():
    assert normalize_city("St. Charles") == normalize_city("Saint Charles")
    assert normalize_city("De Kalb") == normalize_city("DeKalb")


def test_url_encoding_is_idempotent():
    page = "https://www.hslogos.com/States/Illinois/IL_Schools/a_schools/x.html"
    a = normalize_url("../../IL_Images/a_images/Abingdon-Avon%20Tornadoes%201.jpg", page)
    b = normalize_url("../../IL_Images/a_images/Abingdon-Avon Tornadoes 1.jpg", page)
    assert a == b == "https://www.hslogos.com/States/Illinois/IL_Images/a_images/Abingdon-Avon%20Tornadoes%201.jpg"
