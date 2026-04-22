"""
Regrid Parcel API (v2) — commercial nationwide fallback.

Docs: https://support.regrid.com/api/parcel-api-endpoints
Requires ``REGRID_API_TOKEN`` (or ``REGRID_TOKEN``) in the environment or ``params.token``.

Registry params (optional):
  regrid_path — e.g. /us/tx/harris to narrow address search
  limit — max features (default 10)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from core.address.models import NormalizedAddress
from core.scraping.base import BaseScraper
from core.scraping.models import PropertyRecord

logger = logging.getLogger(__name__)

REGRID_ADDRESS_URL = "https://app.regrid.com/api/v2/parcels/address"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PropertyScraper/1.0; +https://regrid.com/api)"
    ),
    "Accept": "application/json",
}


class RegridParcelScraper(BaseScraper):
    name = "us_regrid_parcel"
    requires_browser = False

    def __init__(
        self,
        headless: bool = True,
        source_params: dict | None = None,
        uid: str | None = None,
    ):
        _ = headless, uid
        self.params = source_params or {}
        self._token = (
            self.params.get("token")
            or os.getenv("REGRID_API_TOKEN")
            or os.getenv("REGRID_TOKEN")
        )
        self._path = (self.params.get("regrid_path") or "").strip() or None
        self._limit = min(100, max(1, int(self.params.get("limit", 10))))

    async def close(self) -> None:
        return

    async def health_check(self) -> bool:
        if not self._token:
            return False
        try:
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=15.0) as client:
                r = await client.get(
                    REGRID_ADDRESS_URL,
                    params={"query": "1 main", "limit": 1, "token": self._token},
                )
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    @staticmethod
    def _feature_to_record(
        feat: dict[str, Any],
        address: NormalizedAddress,
        source_url: str,
    ) -> PropertyRecord:
        props = feat.get("properties") or {}
        fields = props.get("fields") if isinstance(props.get("fields"), dict) else props

        parcel = fields.get("parcelnumb") or fields.get("parcelnumb_no_formatting")
        owner = fields.get("owner") or fields.get("deeded_owner") or ""
        prop_addr = fields.get("address") or props.get("headline") or address.one_line
        try:
            pv = float(fields.get("parval") or 0)
            assessed = pv if pv > 0 else None
        except (TypeError, ValueError):
            assessed = None

        return PropertyRecord(
            parcel_number=str(parcel).strip() if parcel else None,
            owner_name=str(owner).strip() if owner else None,
            property_address=str(prop_addr).strip() if prop_addr else None,
            assessed_value=assessed,
            source_url=source_url,
            source_name=RegridParcelScraper.name,
            scraped_at=datetime.now(timezone.utc),
            confidence=0.65,
            raw_html=json.dumps(fields, default=str)[:50000],
        )

    async def scrape(self, address: NormalizedAddress) -> PropertyRecord | None:
        if not self._token:
            logger.info("Regrid skipped: set REGRID_API_TOKEN in the environment")
            return None

        q = (address.raw_input or address.one_line or "").strip()
        if len(q) < 5:
            return None

        params: dict[str, Any] = {
            "query": q,
            "token": self._token,
            "limit": self._limit,
            "return_parcels": "true",
        }
        if self._path:
            params["path"] = self._path

        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=httpx.Timeout(30.0, connect=15.0),
            follow_redirects=True,
        ) as client:
            try:
                r = await client.get(REGRID_ADDRESS_URL, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                logger.warning("Regrid API request failed: %s: %s", type(exc).__name__, exc)
                return None

        parcels = data.get("parcels") or {}
        features = parcels.get("features") if isinstance(parcels, dict) else None
        if not features:
            logger.info("Regrid returned no parcel features for query")
            return None

        # Pick best: first feature, or match zip in fields if possible
        zip5 = (address.zip_code or "")[:5]
        chosen = features[0]
        if zip5 and len(features) > 1:
            for feat in features:
                fld = (feat.get("properties") or {}).get("fields") or {}
                if not isinstance(fld, dict):
                    fld = feat.get("properties") or {}
                mz = str(fld.get("mail_zip") or fld.get("szip") or "")
                if zip5 in mz:
                    chosen = feat
                    break

        src = f"{REGRID_ADDRESS_URL}?{urlencode(params)}"
        return self._feature_to_record(chosen, address, src)
