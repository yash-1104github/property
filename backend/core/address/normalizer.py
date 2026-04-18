import logging
import re
from typing import Any

import usaddress

from core.address.geocode_nominatim import geocode_address_structured
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


def normalize_address(
    raw: str,
    county: str | None = None,
    country_code: str | None = None,
) -> NormalizedAddress:
    """
    Parse an address into structured components.

    - Default (no ``country_code`` or ``country_code=US``): US parsing via ``usaddress``.
    - Non-US: set ``country_code`` to ISO 3166-1 alpha-2 (e.g. ``GB``, ``DE``, ``IN``) and
      we structure the query via OpenStreetMap Nominatim (see ``NOMINATIM_USER_AGENT``).
    """
    raw = re.sub(r"\s+", " ", raw.strip())

    cc = (country_code or "").strip().upper() or None
    if cc and cc != "US":
        return _normalize_international(raw, county_hint=county, country_code=cc)

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


def _india_address_heuristic(raw: str) -> dict[str, Any] | None:
    """
    When Nominatim is unavailable, infer city/state/PIN from common
    '..., City, State 560xxx' trailing pattern (Bengaluru, etc.).
    """
    m = re.search(
        r",\s*([^,]+),\s*([A-Za-z][A-Za-z\s]+)\s+(\d{6})\s*$",
        raw.strip(),
    )
    if not m:
        return None
    city = m.group(1).strip()
    state = m.group(2).strip()
    pin = m.group(3)
    head = raw[: m.start()].strip()
    lower = raw.lower()
    county: str | None = None
    if "bengaluru" in lower or "bangalore" in lower:
        county = "Bengaluru Urban"
    street_number = None
    street_name = None
    tail = head.split(",")[-1].strip() if head else ""
    hm = re.match(r"^(\d+[A-Za-z]?)\s+(.+)$", tail)
    if hm:
        street_number, street_name = hm.group(1), hm.group(2)
    return {
        "raw_input": raw,
        "street_number": street_number,
        "street_name": street_name,
        "city": city,
        "state": state,
        "zip_code": pin,
        "county": county,
        "country": "IN",
        "latitude": None,
        "longitude": None,
    }


def _normalize_international(
    raw: str,
    county_hint: str | None,
    country_code: str,
) -> NormalizedAddress:
    """Worldwide path: bias geocoder with ISO country; map admin areas for registry lookup."""
    data = geocode_address_structured(raw, country_code=country_code)
    if not data and country_code == "IN":
        data = _india_address_heuristic(raw)
        if data:
            logger.info("Used India address heuristic (Nominatim miss or offline)")

    if not data:
        logger.warning("Nominatim returned no result for %r (country=%s)", raw, country_code)
        return NormalizedAddress(
            raw_input=raw,
            county=county_hint,
            country=country_code,
            pipeline_id=_resolve_pipeline(
                {"country": country_code, "state": None, "county": county_hint}
            ),
        )

    fields: dict = {
        "raw_input": raw,
        "street_number": data.get("street_number"),
        "street_name": data.get("street_name"),
        "city": data.get("city"),
        "state": data.get("state"),
        "zip_code": data.get("zip_code"),
        "county": data.get("county") or county_hint,
        "country": data.get("country") or country_code,
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
    }
    if county_hint and not fields.get("county"):
        fields["county"] = county_hint

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


def _slug(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    return re.sub(r"_+", "_", s).strip("_")


def _resolve_pipeline(fields: dict) -> str | None:
    """Stable id for registry lookup: ``us_mi_calhoun`` (US) or ``gb_england_surrey`` (international)."""
    cc = (fields.get("country") or "US").upper()
    state = fields.get("state") or ""
    county = fields.get("county") or ""
    st = _slug(state)
    co = _slug(county)
    if cc == "US":
        if st and co:
            return f"us_{st}_{co}"
        if st:
            return f"us_{st}"
        return None
    if st and co:
        return f"{cc.lower()}_{st}_{co}"
    if st:
        return f"{cc.lower()}_{st}"
    return cc.lower() if cc else None
