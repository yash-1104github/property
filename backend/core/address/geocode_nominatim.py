"""
Structured addresses outside the US via OpenStreetMap Nominatim (geopy).

Requires a stable User-Agent (set NOMINATIM_USER_AGENT in .env). Respect OSM usage policy:
https://operations.osmfoundation.org/policies/nominatim/
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

logger = logging.getLogger(__name__)


def _ua() -> str:
    return os.getenv("NOMINATIM_USER_AGENT", "property-scraper/0.1 (contact: local-dev)")


def geocode_address_structured(
    raw: str,
    country_code: str | None = None,
) -> dict[str, Any] | None:
    """
    Return fields compatible with NormalizedAddress construction, or None on failure.

    country_code: optional ISO 3166-1 alpha-2 to bias search (e.g. GB, DE, IN).
    """
    raw = re.sub(r"\s+", " ", raw.strip())
    if not raw:
        return None

    geolocator = Nominatim(user_agent=_ua(), timeout=15)
    kw: dict[str, Any] = {
        "exactly_one": True,
        "addressdetails": True,
        "language": "en",
    }
    if country_code:
        kw["country_codes"] = country_code.lower()

    try:
        loc = geolocator.geocode(raw, **kw)
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.warning("Nominatim geocode failed: %s", e)
        return None

    if not loc or not getattr(loc, "raw", None):
        return None

    addr = loc.raw.get("address") or {}
    road = addr.get("road") or addr.get("pedestrian") or addr.get("path") or ""
    house = addr.get("house_number") or ""

    cc = (addr.get("country_code") or "").upper()
    if not cc and country_code:
        cc = country_code.upper()

    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("hamlet")
    )
    state = addr.get("state") or addr.get("region") or addr.get("province")
    county = addr.get("county") or addr.get("state_district")
    postcode = addr.get("postcode")

    # Split street_number / name when possible
    street_number: str | None = house or None
    street_name: str | None = road or None
    if not house and road:
        m = re.match(r"^(\d+[A-Za-z]?)\s+(.+)$", road.strip())
        if m:
            street_number, street_name = m.group(1), m.group(2)

    return {
        "raw_input": raw,
        "street_number": street_number,
        "street_name": street_name,
        "city": city,
        "state": state,
        "zip_code": postcode,
        "county": county,
        "country": cc or "ZZ",
        "latitude": loc.latitude,
        "longitude": loc.longitude,
    }
