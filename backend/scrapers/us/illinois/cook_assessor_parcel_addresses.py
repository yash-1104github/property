"""
Cook County Assessor — Parcel Addresses (Socrata SODA API).

Dataset ``3723-97qp`` (situs + mailing fields, monthly refresh). Resolves a
normalized street address + ZIP to PIN / owner mailing fields without the
Cook ArcGIS hosted layer (which is often slow or blocked for automated clients).

After a PIN match, the scraper loads **Assessor — Assessed Values** (``uzyt-m557``)
by PIN so ``tax_history`` lists each roll year with ``assessed_total`` (certified,
then board, then mailed columns). That is **assessed value history**.

When ``treasurer_tax_enrich`` is true (default), it then opens the **Cook County
Treasurer** “Your Property Tax Overview” PIN search in a headless browser and
merges ``total_tax``, ``total_paid``, ``total_due``, and ``last_paid`` into
matching ``tax_history`` years where the site exposes them (layout-dependent;
requires ``playwright`` + ``chromium`` installed).

Docs / portal: https://datacatalog.cookcountyil.gov/d/3723-97qp

Registry params:
  resource_url — SODA resource endpoint (default: Cook parcel addresses JSON).
  assessed_values_resource_url — Assessed Values JSON resource (default Cook ``uzyt-m557``).
  treasurer_tax_enrich — Run Treasurer overview enrichment (default: true).
  treasurer_headless — Playwright headless for Treasurer only (default: scraper ``headless`` arg;
              override with env ``COOK_TREASURER_HEADLESS``). Headless runs often get no bill HTML.
  treasurer_search_url — Treasurer PIN search page URL (has a sensible default).
  loan_history_enrich — Query Cook open-data mortgage extracts by PIN (default: true).
  mortgages_2011_resource_url / mortgages_2012_resource_url / mortgages_2013_2015_resource_url — Socrata JSON endpoints.
  clerk_loan_scrape — When true, probe the Clerk recording portal (often Cloudflare-blocked); prefer env ``COOK_CLERK_RECORDING_SCRAPE=true``.
  app_token — Optional Socrata application token; else ``COOK_COUNTY_SOCRATA_APP_TOKEN``
              or ``SODA_APP_TOKEN`` environment variables.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from core.address.models import NormalizedAddress
from core.scraping.base import BaseScraper
from core.scraping.models import LoanRecord, PropertyRecord, TaxRecord
from scrapers.us.illinois.cook_clerk_recording_loans import try_fetch_clerk_loan_records
from scrapers.us.illinois.cook_treasurer_tax import enrich_record_with_treasurer_overview

logger = logging.getLogger(__name__)

DEFAULT_RESOURCE = "https://datacatalog.cookcountyil.gov/resource/3723-97qp.json"
DEFAULT_ASSESSED_VALUES = "https://datacatalog.cookcountyil.gov/resource/uzyt-m557.json"
DEFAULT_MORTGAGES_2011 = "https://datacatalog.cookcountyil.gov/resource/33fu-uwca.json"
DEFAULT_MORTGAGES_2012 = "https://datacatalog.cookcountyil.gov/resource/myuk-usmm.json"
DEFAULT_MORTGAGES_2013_2015 = "https://datacatalog.cookcountyil.gov/resource/4f2q-h3b7.json"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PropertyScraper/1.0; "
        "+https://datacatalog.cookcountyil.gov)"
    ),
    "Accept": "application/json",
}


def _squash_ws(s: str) -> str:
    return " ".join((s or "").split())


def _soql_str(s: str) -> str:
    return (s or "").replace("'", "''")


def _coerce_bool_param(raw: Any, *, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _floatish(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _assessed_row_to_tax_record(row: dict[str, Any]) -> TaxRecord | None:
    raw_y = row.get("year")
    if raw_y is None or str(raw_y).strip() == "":
        return None
    try:
        year = int(str(raw_y).split(".")[0])
    except ValueError:
        return None
    tot = (
        _floatish(row.get("certified_tot"))
        or _floatish(row.get("board_tot"))
        or _floatish(row.get("mailed_tot"))
    )
    return TaxRecord(year=year, assessed_total=tot)


class CookAssessorParcelAddressesScraper(BaseScraper):
    name = "us_cook_assessor_parcel_addresses"
    requires_browser = False

    def __init__(
        self,
        headless: bool = True,
        source_params: dict | None = None,
        uid: str | None = None,
    ):
        _ = uid
        self._headless = headless
        self.params = source_params or {}
        self._resource = (self.params.get("resource_url") or DEFAULT_RESOURCE).rstrip()
        self._assessed_resource = (
            self.params.get("assessed_values_resource_url") or DEFAULT_ASSESSED_VALUES
        ).rstrip()
        le = self.params.get("loan_history_enrich", True)
        if le is None:
            le = True
        self._loan_enrich = le is True or str(le).lower() in ("1", "true", "yes")
        self._mortgages_2011_resource = (
            self.params.get("mortgages_2011_resource_url") or DEFAULT_MORTGAGES_2011
        ).rstrip()
        self._mortgages_2012_resource = (
            self.params.get("mortgages_2012_resource_url") or DEFAULT_MORTGAGES_2012
        ).rstrip()
        self._mortgages_2013_2015_resource = (
            self.params.get("mortgages_2013_2015_resource_url") or DEFAULT_MORTGAGES_2013_2015
        ).rstrip()
        if "clerk_loan_scrape" in self.params:
            ck = self.params["clerk_loan_scrape"]
        else:
            ck = os.getenv("COOK_CLERK_RECORDING_SCRAPE", "").lower() in ("1", "true", "yes")
        self._clerk_loan_scrape = ck is True or str(ck).lower() in ("1", "true", "yes")
        te = self.params.get("treasurer_tax_enrich", True)
        if te is None:
            te = True
        self._treasurer_enrich = te is True or str(te).lower() in ("1", "true", "yes")
        self._treasurer_search_url = (
            self.params.get("treasurer_search_url") or ""
        ).strip() or "https://www.cookcountytreasurer.com/yourpropertytaxoverviewsearch.aspx"
        if "treasurer_headless" in self.params:
            self._treasurer_headless = _coerce_bool_param(
                self.params["treasurer_headless"], default=headless
            )
        elif os.getenv("COOK_TREASURER_HEADLESS", "").strip() != "":
            self._treasurer_headless = _coerce_bool_param(
                os.getenv("COOK_TREASURER_HEADLESS", ""), default=headless
            )
        else:
            self._treasurer_headless = headless
        self._app_token = (
            self.params.get("app_token")
            or os.getenv("COOK_COUNTY_SOCRATA_APP_TOKEN")
            or os.getenv("SODA_APP_TOKEN")
            or ""
        ).strip()
        self._max_attempts = max(1, int(self.params.get("max_http_attempts", 3)))
        self._timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=30.0)

    async def close(self) -> None:
        return

    def _headers(self) -> dict[str, str]:
        h = dict(DEFAULT_HEADERS)
        if self._app_token:
            h["X-App-Token"] = self._app_token
        return h

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(headers=self._headers(), timeout=20.0) as client:
                r = await client.get(self._resource, params={"$limit": 1})
                return r.status_code == 200 and r.text.strip().startswith("[")
        except httpx.HTTPError:
            return False

    @staticmethod
    def _where_clauses(address: NormalizedAddress) -> list[str]:
        zip5 = (address.zip_code or "")[:5]
        if not zip5:
            return []
        z = _soql_str(zip5)
        clauses: list[str] = []
        seen: set[str] = set()

        def add(w: str) -> None:
            if w and w not in seen:
                seen.add(w)
                clauses.append(w)

        line_up = _squash_ws(address.full_street).upper()
        city_up = _squash_ws(address.city or "").upper()
        if line_up:
            add(f"upper(prop_address_full) = '{_soql_str(line_up)}' AND prop_address_zipcode_1 = '{z}'")
            if city_up:
                add(
                    f"upper(prop_address_full) = '{_soql_str(line_up)}' AND prop_address_zipcode_1 = '{z}' "
                    f"AND upper(prop_address_city_name) = '{_soql_str(city_up)}'"
                )

        num = (address.street_number or "").strip()
        name = (address.street_name or "").strip().upper()
        suf = (address.street_suffix or "").strip().upper()
        if num and name:
            if suf:
                add(
                    f"prop_address_zipcode_1 = '{z}' AND upper(prop_address_full) LIKE "
                    f"'%{_soql_str(num)}%{_soql_str(name)}%{_soql_str(suf)}%'"
                )
            else:
                add(
                    f"prop_address_zipcode_1 = '{z}' AND upper(prop_address_full) LIKE "
                    f"'%{_soql_str(num)}%{_soql_str(name)}%'"
                )

        return clauses

    @staticmethod
    def _row_to_record(
        row: dict[str, Any],
        address: NormalizedAddress,
        source_url: str,
        *,
        tax_history: list[TaxRecord] | None = None,
        assessed_value: float | None = None,
    ) -> PropertyRecord:
        pin = (row.get("pin") or "").strip() or None
        prop_line = (row.get("prop_address_full") or "").strip()
        city = (row.get("prop_address_city_name") or "").strip()
        st = (row.get("prop_address_state") or "").strip()
        z = (row.get("prop_address_zipcode_1") or "").strip()
        prop_addr = ", ".join(p for p in (prop_line, city, f"{st} {z}".strip()) if p) or address.one_line

        owner = (row.get("owner_address_name") or "").strip() or None
        if not owner:
            owner = (row.get("mail_address_name") or "").strip() or None

        def _addr_line(prefix: str) -> str | None:
            parts = [
                row.get(f"{prefix}_full"),
                row.get(f"{prefix}_city_name"),
                " ".join(
                    p
                    for p in (
                        (row.get(f"{prefix}_state") or "").strip(),
                        (row.get(f"{prefix}_zipcode_1") or "").strip(),
                    )
                    if p
                ),
            ]
            line = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
            return line or None

        owner_line = _addr_line("owner_address") or _addr_line("mail_address")

        if tax_history is not None:
            tax_hist = tax_history
        else:
            tax_year: int | None = None
            raw_y = row.get("year")
            if raw_y is not None and str(raw_y).strip() != "":
                try:
                    tax_year = int(str(raw_y).split(".")[0])
                except ValueError:
                    tax_year = None
            tax_hist = [TaxRecord(year=tax_year)] if tax_year is not None else []

        return PropertyRecord(
            parcel_number=pin,
            owner_name=owner,
            owner_address=owner_line,
            property_address=prop_addr,
            assessed_value=assessed_value,
            tax_history=tax_hist,
            source_url=source_url,
            source_name=CookAssessorParcelAddressesScraper.name,
            scraped_at=datetime.now(timezone.utc),
            confidence=0.82,
            raw_html=json.dumps(row, default=str),
        )

    async def _soda_get_list(
        self,
        client: httpx.AsyncClient,
        resource_url: str,
        params: dict[str, str | int],
    ) -> list[Any] | None:
        data: list[Any] | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                r = await client.get(resource_url, params=params)
                r.raise_for_status()
                parsed = r.json()
                if isinstance(parsed, dict):
                    err = parsed.get("error") or parsed.get("message") or parsed
                    logger.warning(
                        "Cook SODA non-list JSON from %s params=%r: %s",
                        resource_url,
                        params,
                        err,
                    )
                    data = None
                else:
                    data = parsed if isinstance(parsed, list) else None
                break
            except (httpx.HTTPStatusError, httpx.TransportError, json.JSONDecodeError) as e:
                logger.warning(
                    "Cook SODA GET %s attempt %d/%d: %s: %s",
                    resource_url,
                    attempt,
                    self._max_attempts,
                    type(e).__name__,
                    e,
                )
                if attempt >= self._max_attempts:
                    break
                await asyncio.sleep(1.0 * attempt)
        return data

    async def _fetch_assessed_tax_history(
        self, client: httpx.AsyncClient, pin: str
    ) -> tuple[list[TaxRecord], float | None]:
        """Load per-year assessed totals from Cook Assessed Values (by PIN)."""
        pin = str(pin or "").strip()
        if not pin:
            return [], None
        params: dict[str, str | int] = {
            "$where": f"pin = '{_soql_str(pin)}'",
            "$order": "year DESC",
            "$limit": 35,
        }
        rows = await self._soda_get_list(client, self._assessed_resource, params)
        if rows is None:
            logger.warning(
                "Cook assessed-values request failed (see prior logs); PIN=%s resource=%s",
                pin,
                self._assessed_resource,
            )
            return [], None
        if len(rows) == 0:
            logger.warning(
                "Cook assessed-values returned 0 rows for PIN=%s (SoQL pin match)",
                pin,
            )
            return [], None
        tax_rows: list[TaxRecord] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            tr = _assessed_row_to_tax_record(item)
            if tr is not None:
                tax_rows.append(tr)
        latest_av: float | None = None
        for tr in tax_rows:
            if tr.assessed_total is not None:
                latest_av = tr.assessed_total
                break
        return tax_rows, latest_av

    async def _maybe_treasurer_enrich(self, record: PropertyRecord, pin: str) -> PropertyRecord:
        if not self._treasurer_enrich or len(re.sub(r"\D", "", pin)) != 14:
            return record
        try:
            return await enrich_record_with_treasurer_overview(
                record,
                headless=self._treasurer_headless,
                search_url=self._treasurer_search_url,
            )
        except Exception:
            logger.exception("Cook Treasurer enrichment failed; returning assessor-only record")
            return record

    async def _fetch_loan_history(self, client: httpx.AsyncClient, pin: str) -> list[LoanRecord]:
        if not self._loan_enrich:
            return []
        pin = str(pin or "").strip()
        if not pin:
            return []

        async def _get(resource: str, where: str, limit: int = 25) -> list[dict]:
            params: dict[str, str | int] = {
                "$where": where,
                "$order": "recorded_date DESC",
                "$limit": limit,
            }
            data = await self._soda_get_list(client, resource, params)
            if not data:
                return []
            return [x for x in data if isinstance(x, dict)]

        rows: list[dict] = []
        rows.extend(await _get(self._mortgages_2011_resource, f"pin = '{_soql_str(pin)}'"))
        rows.extend(await _get(self._mortgages_2012_resource, f"pin = '{_soql_str(pin)}'"))
        rows.extend(await _get(self._mortgages_2013_2015_resource, f"pin = '{_soql_str(pin)}'"))

        out: list[LoanRecord] = []
        seen: set[tuple[str, str]] = set()

        for r in rows:
            doc = str(r.get("doc_number") or r.get("document_number") or "").strip()
            rdate = str(r.get("recorded_date") or "").strip()
            key = (doc, rdate)
            if key in seen:
                continue
            seen.add(key)

            amount = _floatish(r.get("consideration_amount"))
            doc_type = str(r.get("doc_type") or r.get("document_type") or "").strip() or None
            exec_date = str(r.get("execution_date") or "").strip() or None
            prop_addr = (
                str(r.get("location_1_address") or r.get("location_address") or r.get("street") or "").strip()
                or None
            )
            out.append(
                LoanRecord(
                    recorded_date=rdate or None,
                    execution_date=exec_date,
                    amount=amount,
                    document_number=doc or None,
                    document_type=doc_type,
                    property_address=prop_addr,
                    source_name="cook_recorder_mortgages_open_data",
                )
            )

        out.sort(key=lambda x: (x.recorded_date or ""), reverse=True)

        if self._clerk_loan_scrape:
            extra = await try_fetch_clerk_loan_records(pin, headless=self._headless)
            if extra:
                keys = {(x.document_number or "", x.recorded_date or "") for x in out}
                for lr in extra:
                    k = (lr.document_number or "", lr.recorded_date or "")
                    if k in keys:
                        continue
                    keys.add(k)
                    out.append(lr)
                out.sort(key=lambda x: (x.recorded_date or ""), reverse=True)
        return out

    async def scrape(self, address: NormalizedAddress) -> PropertyRecord | None:
        clauses = self._where_clauses(address)
        if not clauses:
            logger.warning("Cook parcel addresses: missing ZIP or street parts for %s", address.one_line)
            return None

        async with httpx.AsyncClient(
            headers=self._headers(),
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            for where in clauses:
                params: dict[str, str | int] = {
                    "$where": where,
                    "$order": "year DESC",
                    "$limit": 5,
                }
                source_url = f"{self._resource}?{urlencode(params)}"
                data = await self._soda_get_list(client, self._resource, params)

                if not data:
                    logger.debug("Cook SODA 0 rows for $where=%s", where[:160])
                    continue

                row = data[0]
                if not isinstance(row, dict):
                    continue
                pin = str(row.get("pin") or "").strip()
                tax_hist, latest_assessed = await self._fetch_assessed_tax_history(client, pin)
                loans = await self._fetch_loan_history(client, pin)
                if not tax_hist:
                    logger.warning(
                        "Cook assessed-values unavailable for PIN %s — tax_history will only "
                        "reflect the parcel-address row (year only). Restart the API after upgrading, "
                        "and ensure outbound HTTPS to %s is allowed.",
                        pin or "?",
                        self._assessed_resource,
                    )
                    rec = self._row_to_record(row, address, source_url)
                    if loans:
                        rec.loan_history = loans
                    return await self._maybe_treasurer_enrich(rec, pin)
                rec = self._row_to_record(
                    row,
                    address,
                    source_url,
                    tax_history=tax_hist,
                    assessed_value=latest_assessed,
                )
                if loans:
                    rec.loan_history = loans
                return await self._maybe_treasurer_enrich(rec, pin)

        logger.info("Cook parcel addresses: no row matched for %s", address.one_line)
        return None
