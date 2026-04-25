from __future__ import annotations

import logging
import re

import usaddress

from core.address.models import NormalizedAddress

logger = logging.getLogger(__name__)

TAG_MAP = {
    "AddressNumber": "street_number",
    "StreetName": "street_name",
    "StreetNamePostType": "street_suffix",
    "StreetNamePreDirectional": "street_direction",
    "StreetNamePostDirectional": "street_direction",
    "OccupancyIdentifier": "unit",
    "OccupancyType": "unit",
    "PlaceName": "city",
    "StateName": "state",
    "ZipCode": "zip_code",
}

STATE_ABBREVS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}

# When ZIP is missing, infer county from major city (add more as needed).
CITY_COUNTY_HINTS = {
    ("IL", "chicago"): "Cook",
}

COUNTY_LOOKUP = {
    ("MI", "49017"): "Calhoun",
    ("MI", "49014"): "Calhoun",
    ("MI", "49015"): "Calhoun",
    ("MI", "49037"): "Calhoun",
    # Harris County TX (Houston area — examples; pass explicit county when unsure)
    ("TX", "77002"): "Harris",
    ("TX", "77003"): "Harris",
    # Maricopa County AZ (examples)
    ("AZ", "85003"): "Maricopa",
    ("AZ", "85004"): "Maricopa",
    # Cook County IL / Chicago (sample ZIPs; city hint also maps Chicago → Cook)
    ("IL", "60630"): "Cook",
    ("IL", "60640"): "Cook",
    ("IL", "60618"): "Cook",
    ("IL", "60657"): "Cook",
}


def normalize_address(raw: str, county: str | None = None) -> NormalizedAddress:
    """Parse a US address string into structured components (usaddress + ZIP→county hints)."""
    raw = re.sub(r"\s+", " ", raw.strip())

    try:
        tagged, addr_type = usaddress.tag(raw)
    except usaddress.RepeatedLabelError:
        logger.warning("usaddress failed to parse: %s, using fallback", raw)
        return _fallback_parse(raw, county)

    fields: dict = {"raw_input": raw, "country": "US"}
    for component_name, text_value in tagged.items():
        field = TAG_MAP.get(component_name)
        if field:
            existing = fields.get(field)
            if existing:
                fields[field] = f"{existing} {text_value}"
            else:
                fields[field] = text_value

    if fields.get("state"):
        state = fields["state"]
        if len(state) > 2:
            fields["state"] = STATE_ABBREVS.get(state.lower(), state.upper())
        else:
            fields["state"] = state.upper()

    if fields.get("zip_code"):
        z = fields["zip_code"].split("-")[0].strip()
        fields["zip_code"] = z

    if county:
        fields["county"] = county
    elif fields.get("state") and fields.get("zip_code"):
        key = (fields["state"], fields["zip_code"])
        fields["county"] = COUNTY_LOOKUP.get(key)
    if not fields.get("county") and fields.get("state") and fields.get("city"):
        ck = (fields["state"], fields["city"].strip().lower())
        fields["county"] = CITY_COUNTY_HINTS.get(ck)

    fields["pipeline_id"] = _resolve_pipeline(fields)

    return NormalizedAddress(**fields)


def _fallback_parse(raw: str, county: str | None) -> NormalizedAddress:
    """Regex-based fallback for addresses usaddress can't handle."""
    pattern = r"^(\d+)\s+(.+?),\s*(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
    m = re.match(pattern, raw, re.IGNORECASE)
    if m:
        zip_code = m.group(5).split("-")[0].strip()
        fb: dict = {
            "raw_input": raw,
            "street_number": m.group(1),
            "street_name": m.group(2),
            "city": m.group(3),
            "state": m.group(4).upper(),
            "zip_code": zip_code,
            "county": county,
            "country": "US",
        }
        if not county and fb.get("state") and fb.get("zip_code"):
            fb["county"] = COUNTY_LOOKUP.get((fb["state"], fb["zip_code"]), county)
        if not fb.get("county") and fb.get("state") and fb.get("city"):
            fb["county"] = CITY_COUNTY_HINTS.get((fb["state"], fb["city"].strip().lower()))
        fb["pipeline_id"] = _resolve_pipeline(fb)
        return NormalizedAddress(**fb)
    fb = {"raw_input": raw, "county": county, "country": "US"}
    fb["pipeline_id"] = _resolve_pipeline(fb)
    return NormalizedAddress(**fb)


def _slug(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    return re.sub(r"_+", "_", s).strip("_")


def _resolve_pipeline(fields: dict) -> str | None:
    """Registry id, e.g. ``us_mi_calhoun``."""
    state = fields.get("state") or ""
    county = fields.get("county") or ""
    st = _slug(state)
    co = _slug(county)
    if st and co:
        return f"us_{st}_{co}"
    if st:
        return f"us_{st}"
    return None

