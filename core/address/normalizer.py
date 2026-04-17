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

COUNTY_LOOKUP = {
    ("MI", "49017"): "Calhoun",
    ("MI", "49014"): "Calhoun",
    ("MI", "49015"): "Calhoun",
    ("MI", "49037"): "Calhoun",
}


def normalize_address(raw: str, county: str | None = None) -> NormalizedAddress:
    """Parse a raw US address string into structured components."""
    raw = re.sub(r"\s+", " ", raw.strip())

    try:
        tagged, addr_type = usaddress.tag(raw)
    except usaddress.RepeatedLabelError:
        logger.warning("usaddress failed to parse: %s, using fallback", raw)
        return _fallback_parse(raw, county)

    # usaddress.tag returns {component_name: text_value}, e.g. StreetName -> "Main"
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
        fb["pipeline_id"] = _resolve_pipeline(fb)
        return NormalizedAddress(**fb)
    return NormalizedAddress(raw_input=raw, county=county, country="US")


def _resolve_pipeline(fields: dict) -> str | None:
    """Determine which scraping pipeline to use based on location."""
    state = fields.get("state", "").upper()
    county = (fields.get("county") or "").lower().replace(" ", "_")
    if state and county:
        return f"us_{state.lower()}_{county}"
    if state:
        return f"us_{state.lower()}"
    return None
