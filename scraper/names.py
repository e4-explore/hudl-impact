"""School-name and city normalisation used for fuzzy matching."""

from __future__ import annotations

import re
import unicodedata

# Multi-word phrases removed before tokenising (order matters: longest first).
PHRASES = [
    r"junior\s*/?\s*senior high school", r"jr\s*/?\s*sr high school", r"senior high school",
    r"sr high school", r"junior senior high", r"jr sr high", r"senior high", r"sr high",
    r"middle\s*/?\s*high school", r"jr\s*and\s*sr", r"high school", r"h\s*s", r"jr\s*/\s*sr",
    r"k\s*12",
]

# Single tokens dropped entirely: generic words that differ between sources.
STOP_TOKENS = {
    "high", "school", "schools", "hs", "senior", "sr", "community", "comm", "the", "of",
    "jr", "junior", "secondary", "campus", "twp", "township", "middle", "sch", "network",
    "chtr", "charter", "and",
}

# Abbreviation expansion applied token by token.
EXPAND = {
    "st": "saint", "ste": "sainte", "mt": "mount", "ft": "fort", "ctr": "center", "centre": "center",
    "acad": "academy", "acdmy": "academy", "prep": "preparatory", "coll": "college",
    "cath": "catholic", "chr": "christian", "intl": "international", "tech": "technical",
    "voc": "vocational", "cent": "central", "co": "county", "jfk": "john f kennedy",
    "mlk": "martin luther king", "&": "and", "inst": "institute",
}

# Single-letter compass abbreviations, only expanded as the first word
# ("N Chicago" -> "north chicago" but "Adlai E Stevenson" keeps its initial).
LEADING_EXPAND = {"n": "north", "s": "south", "e": "east", "w": "west", "no": "north", "so": "south"}


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalize_name(name: str) -> str:
    s = ascii_fold(name or "").lower()
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"\bst\.", "saint ", s)  # "St." -> Saint before punctuation is stripped
    s = re.sub(r"[^a-z0-9/ ]+", " ", s)  # hyphens, periods, commas... -> space
    for phrase in PHRASES:
        s = re.sub(r"\b" + phrase + r"\b", " ", s)
    s = s.replace("/", " ")
    tokens = []
    for i, tok in enumerate(s.split()):
        tok = (LEADING_EXPAND.get(tok) if i == 0 else None) or EXPAND.get(tok, tok)
        tokens.extend(tok.split())
    kept = [t for t in tokens if t not in STOP_TOKENS]
    # "Community High School" would normalise to nothing; keep the words then.
    return " ".join(kept or tokens)


def normalize_city(city: str) -> str:
    s = ascii_fold(city or "").lower()
    s = re.sub(r"\bst\.?\b", "saint", s)
    s = re.sub(r"\bmt\.?\b", "mount", s)
    s = re.sub(r"\bft\.?\b", "fort", s)
    return re.sub(r"[^a-z0-9]+", "", s)
