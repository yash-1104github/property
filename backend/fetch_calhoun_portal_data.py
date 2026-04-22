#!/usr/bin/env python3
"""
Pull public parcel attributes from the Calhoun County layer (Battle Creek ArcGIS REST).

Usage (from repo root):
  python3 backend/fetch_calhoun_portal_data.py
  python3 backend/fetch_calhoun_portal_data.py "21013 DANA Drive, Battle Creek, MI 49017"

Requires network access to https://gis.battlecreekmi.gov (run on your machine / VPN if blocked).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.address.normalizer import normalize_address
from scrapers.us.michigan.arcgis_parcel_query import ArcGISParcelQueryScraper

LAYER = "https://gis.battlecreekmi.gov/mapping/rest/services/Basemap/FeatureServer/11"


async def run(address_line: str, county: str | None) -> None:
    addr = normalize_address(address_line, county=county)
    scraper = ArcGISParcelQueryScraper(
        source_params={"layer_url": LAYER},
    )
    try:
        record = await scraper.scrape(addr)
    finally:
        await scraper.close()

    if record is None:
        print("No parcel feature matched this address on the ArcGIS layer.", file=sys.stderr)
        print("Try the same address in a browser:", file=sys.stderr)
        print("  https://gis.battlecreekmi.gov/mapping/rest/services/Basemap/FeatureServer/11", file=sys.stderr)
        print("  https://app.fetchgis.com/?currentMap=calhoun", file=sys.stderr)
        print("  https://bsaonline.com/?uid=662", file=sys.stderr)
        sys.exit(1)

    out = {
        "normalized_address": addr.model_dump(),
        "property_record": record.model_dump(mode="json"),
    }
    print(json.dumps(out, indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch Calhoun parcel data via ArcGIS REST")
    p.add_argument(
        "address",
        nargs="?",
        default="21013 DANA Drive, Battle Creek, MI 49017",
        help="US address string",
    )
    p.add_argument("--county", default="Calhoun", help="County hint for normalization")
    args = p.parse_args()
    asyncio.run(run(args.address, args.county))


if __name__ == "__main__":
    main()
