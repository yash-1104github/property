"""
Regrid Parcel API (v2) — commercial nationwide fallback.

Docs: https://support.regrid.com/api/parcel-api-endpoints
Requires ``REGRID_API_TOKEN`` (or ``REGRID_TOKEN``) in the environment or ``params.token``.

Registry params (optional):
  regrid_path — e.g. /us/tx/harris to narrow address search
  limit — max features (default 10)

ROOT CAUSE & FIX (Regrid 403 → empty)
------------------------------------
- Some tokens/accounts return HTTP 403 when a `path` is provided (e.g. `/us/il/cook/chicago`).
  This scraper already retries once without `path`.

- If the no-path retry returns HTTP 200 but **no parcel features**, that is typically a
  Regrid plan/token coverage limitation for the requested geography (subscription issue),
  not a code bug. Verify your Regrid plan includes parcel coverage for the region.
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
from core.scraping.models import PropertyRecord, SaleRecord

logger = logging.getLogger(__name__)

REGRID_ADDRESS_URL = "https://app.regrid.com/api/v2/parcels/address"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PropertyScraper/1.0; +https://regrid.com/api)"
    ),
    "Accept": "application/json",
}


def _path_slug(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return None
    s = str(value).strip().lower()
    for old, new in ((".", ""), ("'", ""), (" ", "-")):
        s = s.replace(old, new)
    while "--" in s:
        s = s.replace("--", "-")
    s = s.strip("-")
    return s or None


def regrid_path_for_address(address: NormalizedAddress) -> str | None:
    """
    Regrid Standard Schema path for the address endpoint.

    Prefer /us/{state}/{county}/{city} when city is known (e.g. /us/il/cook/chicago);
    Regrid's examples use city-level paths for metro searches.
    """
    st = _path_slug(address.state)
    if not st:
        return None
    parts = ["us", st]
    co = _path_slug(address.county)
    if co:
        parts.append(co)
    ci = _path_slug(address.city)
    if ci:
        parts.append(ci)
    return "/" + "/".join(parts)


def _source_url_public(params: dict[str, Any]) -> str:
    """Query string for logging / stored source_url — never includes the API token."""
    public = {k: v for k, v in params.items() if k != "token"}
    return f"{REGRID_ADDRESS_URL}?{urlencode(public)}"


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
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=httpx.Timeout(15.0, connect=5.0)) as client:
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

        # Extract sale history from Regrid sales_transactions field
        sale_history: list[SaleRecord] = []
        sales_data = fields.get("sales_transactions") or fields.get("sales") or []
        if isinstance(sales_data, list):
            for sale in sales_data[:10]:  # Limit to 10 recent sales
                if not isinstance(sale, dict):
                    continue
                try:
                    sale_history.append(SaleRecord(
                        date=sale.get("sale_date") or sale.get("sale_dt") or None,
                        price=sale.get("sale_price") or sale.get("price") or None,
                        document_number=sale.get("doc_number") or sale.get("document_number") or None,
                        instrument=sale.get("deed_type") or sale.get("document_type") or None,
                        buyer=sale.get("buyer") or None,
                        seller=sale.get("seller") or None,
                    ))
                except Exception:
                    continue
        # Also check top-level sales array
        if not sale_history:
            top_sales = props.get("sales") or []
            if isinstance(top_sales, list):
                for sale in top_sales[:10]:
                    if isinstance(sale, dict):
                        try:
                            sale_history.append(SaleRecord(
                                date=sale.get("sale_date") or sale.get("sale_dt") or None,
                                price=sale.get("sale_price") or sale.get("price") or None,
                                document_number=sale.get("doc_number") or None,
                                instrument=sale.get("deed_type") or sale.get("document_type") or None,
                            ))
                        except Exception:
                            continue

        return PropertyRecord(
            parcel_number=str(parcel).strip() if parcel else None,
            owner_name=str(owner).strip() if owner else None,
            property_address=str(prop_addr).strip() if prop_addr else None,
            assessed_value=assessed,
            sale_history=sale_history,
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
            r = await client.get(REGRID_ADDRESS_URL, params=params)
            # Some accounts or regions reject county-only paths with 403; retry once without path.
            if r.status_code == 403 and params.get("path"):
                logger.info(
                    "Regrid returned 403 with path=%s; retrying without path",
                    params.get("path"),
                )
                retry_params = {k: v for k, v in params.items() if k != "path"}
                r = await client.get(REGRID_ADDRESS_URL, params=retry_params)
                params = retry_params
            if r.status_code != 200:
                detail = (r.text or "")[:400].replace("\n", " ")
                logger.warning(
                    "Regrid API HTTP %s path=%s detail=%r",
                    r.status_code,
                    params.get("path") or "(none)",
                    detail,
                )
                return None
            try:
                data = r.json()
            except json.JSONDecodeError as exc:
                logger.warning("Regrid API invalid JSON: %s", exc)
                return None

        parcels = data.get("parcels") or {}
        features = parcels.get("features") if isinstance(parcels, dict) else None
        if not features:
            logger.info(
                "Regrid returned no parcel features for query (HTTP 200). "
                "If this repeats for a known-valid address, it is usually token/plan coverage "
                "for this geography rather than a scraper bug."
            )
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

        return self._feature_to_record(chosen, address, _source_url_public(params))
