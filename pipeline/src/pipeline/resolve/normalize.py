"""Address and company-name normalization shared by Stage 1 (facility) and
Stage 2 (corporate) resolution.

Address parsing uses usaddress, not libpostal — all three source addresses
(EPA FRS, OSHA ITA, USASpending recipient) are US-only structured government
filings, not messy scraped text, so usaddress's narrower scope is not a real
limitation, and it avoids libpostal's ~2GB trained-model / C-library install.
"""

import difflib
import math
import re

import usaddress

LEGAL_SUFFIXES = [
    "INCORPORATED", "INC", "CORPORATION", "CORP", "COMPANY", "CO",
    "LLC", "LLP", "LP", "LTD", "LIMITED", "PLC", "GROUP", "HOLDINGS",
    "ENTERPRISES", "INDUSTRIES", "THE",
]
_SUFFIX_RE = re.compile(r"\b(" + "|".join(LEGAL_SUFFIXES) + r")\b\.?")
_WS_RE = re.compile(r"\s+")


def normalize_company_name(name: str) -> str:
    """Strip legal suffixes / punctuation, uppercase, collapse whitespace.

    This is the Stage 2 grouping key before override lookup — deliberately
    aggressive (drops "THE", "INC", "CO", etc.) since the goal is to catch
    "Boeing Company" == "The Boeing Company" == "BOEING CO", not to preserve
    the original name.
    """
    if not name:
        return ""
    n = name.upper()
    n = n.replace("&", " AND ")
    n = re.sub(r"[.,]", " ", n)
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = _SUFFIX_RE.sub(" ", n)
    n = _WS_RE.sub(" ", n).strip()
    return n


def name_similarity(a: str, b: str) -> float:
    """Token-sort-ratio-style similarity in [0, 1], dependency-free (no
    rapidfuzz): sort each name's tokens, then diff the sorted strings. This
    makes "BOEING DISTRIBUTION SERVICES" and "BOEING SERVICES DISTRIBUTION"
    compare as near-identical, unlike a raw SequenceMatcher on the originals.
    """
    ta = " ".join(sorted(normalize_company_name(a).split()))
    tb = " ".join(sorted(normalize_company_name(b).split()))
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()


_STREET_ABBREV = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "ROAD": "RD", "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR",
    "HIGHWAY": "HWY", "PARKWAY": "PKWY", "PLACE": "PL", "SUITE": "STE",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}


def normalize_address(address_line: str, city: str, state: str, zip_code: str) -> str | None:
    """Parse with usaddress, then reassemble into a canonical
    "<number> <street> <city> <state> <zip5>" string for exact Tier-1
    matching. Returns None if usaddress can't parse a street number/name at
    all (e.g. a PO box or an empty/garbage address) — such records fall
    through to geocode-based matching instead, never a false exact-match.
    """
    if not address_line or not address_line.strip():
        return None
    try:
        tagged, _ = usaddress.tag(address_line)
    except usaddress.RepeatedLabelError:
        return None

    number = tagged.get("AddressNumber", "")
    street = " ".join(
        tagged.get(k, "") for k in ("StreetNamePreDirectional", "StreetName", "StreetNamePostType")
    ).strip()
    if not number or not street:
        return None

    street_tokens = [_STREET_ABBREV.get(t, t) for t in street.upper().split()]
    street_norm = " ".join(street_tokens)
    zip5 = (zip_code or "")[:5]
    city_norm = (city or "").upper().strip()
    state_norm = (state or "").upper().strip()
    return f"{number} {street_norm}, {city_norm} {state_norm} {zip5}".strip()


# Must contain a digit -- a real registry-code prefix ("WA317937871 - ",
# "92615 - ") always does; a plain word like "ELECTRO-" in "ELECTRO-MOTIVE"
# must not be mistaken for one.
_LEADING_CODE_RE = re.compile(r"^(?=[A-Z0-9]*\d)[A-Z0-9]{2,}\s*-\s*")
_LEADING_ARTICLE_RE = re.compile(r"^(THE|A|AN)\s+")


def _company_identity_prefix(name: str) -> str:
    """Strip a leading registry-code token (e.g. "WA317937871 - ", "92615 - ")
    and a leading article, leaving what should be the company's own identity
    at the front of the string. FRS in particular prepends state+ID codes to
    a large fraction of facility names."""
    n = (name or "").upper().strip()
    n = _LEADING_CODE_RE.sub("", n)
    n = _LEADING_ARTICLE_RE.sub("", n)
    return n


def matches_as_company(name: str, pattern: str) -> bool:
    """True if `pattern` identifies the company itself — i.e. is a leading,
    word-boundary-respecting token of the name — not merely a substring
    anywhere in it.

    This is deliberately stricter than a plain substring search: company
    names collide with place names constantly (Oshkosh WI, Peoria IL where
    Caterpillar is headquartered, and so on), and "PIONEER METAL FINISHING
    CORP - OSHKOSH DIV" containing "OSHKOSH" as a trailing location qualifier
    is not evidence it's an Oshkosh Corporation facility. A real subsidiary
    or the company itself identifies AS that name up front:
    "OSHKOSH DEFENSE, LLC - WEST PLANT", "95415 - CATERPILLAR PAVING
    PRODUCTS INC". The tradeoff: a DBA phrased as "X: A Boeing Company"
    won't match this way and needs an explicit CANDIDATE_PATTERNS or
    corporate_map.yaml entry once discovered — precision over recall here,
    consistent with "precision on whales over recall on the tail"."""
    identity = _company_identity_prefix(name)
    pattern_u = pattern.upper()
    if not identity.startswith(pattern_u):
        return False
    rest = identity[len(pattern_u):]
    return rest == "" or not rest[0].isalnum()


_GENERIC_LOCATION_LABELS = {
    "plant", "building", "warehouse", "service", "parts", "admin",
    "administration", "headquarters", "main facility", "corporate",
    "audit", "machine shop", "facility", "site", "location", "office", "hq",
}

_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

_NUMERIC_OR_CODE_RE = re.compile(r"^[\d\s_.\-]+$")
_CITY_STATE_RE = re.compile(r"^([A-Za-z .'\-]+),\s*([A-Za-z]{2})(\s*\([\w\d]+\))?$")
_GENERIC_LABEL_RE = re.compile(
    r"^(" + "|".join(re.escape(g) for g in _GENERIC_LOCATION_LABELS) + r")\s*#?\d*$"
)


def looks_like_company_name(name: str | None) -> bool:
    """True if `name` looks like it identifies a company -- contains real
    alphabetic tokens and isn't purely a site code, a bare "City, ST"
    descriptor, or a generic facility-type label. Used to decide whether
    name similarity carries any information at all before gating a merge on
    it (see resolve_facilities' Tier 2b): comparing a real corporate name
    against something that isn't a name to begin with (an OSHA internal
    site code, a bare city) always scores low similarity for a reason that
    has nothing to do with whether the two records are the same facility."""
    if not name:
        return False
    n = name.strip()
    if len(n) < 3:
        return False
    if _NUMERIC_OR_CODE_RE.fullmatch(n):
        return False
    m = _CITY_STATE_RE.fullmatch(n)
    if m and m.group(2).upper() in _US_STATE_CODES:
        return False
    norm = re.sub(r"\s+", " ", n.lower()).strip()
    if _GENERIC_LABEL_RE.fullmatch(norm):
        return False
    return True


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
