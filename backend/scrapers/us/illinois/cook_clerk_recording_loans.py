"""
Cook County Clerk — Mortgage & Loan Recording Scraper
======================================================

ROOT CAUSES & FIXES (empty `loan_history`)
-----------------------------------------
This scraper previously returned empty results due to multiple compounding issues:

1) WRONG PIN FORMAT
   - Assessor provides a raw 14-digit PIN: "13151140390000"
   - Recorder datasets index PIN in dashed format: "13-15-114-039-0000"
   - Fix: always normalize raw → dashed before querying Socrata.

2) WRONG FIELD NAMES (Socrata JSON schema)
   Confirmed fields (snake_case) in the Recorder datasets:
     - recorded_date (NOT recording_date)
     - document_type (NOT doc_type)
     - consideration_amount (NOT consideration)
     - document_number (NOT instrument_number)

3) WRONG DATASETS
   Year-batch exports like 33fu-uwca (2011), myuk-usmm (2012-Jan), 4f2q-h3b7 (2013-2015)
   are partial-year and are not suitable as a primary "full history" lookup.

   Fix:
     - Primary dataset: fc9e-k9vb (full/live Recorder dataset)
     - Fallback dataset: 4f2q-h3b7 (partial years; only as fallback)

4) PIN HANDOFF (pipeline integration)
   - If the orchestrator doesn’t pass the assessor PIN forward, this scraper must fall back
     to address lookup, which is less reliable.
   - In this backend, the pipeline calls `try_fetch_clerk_loan_records(record.parcel_number, ...)`.

Data Source
-----------
Cook County Recorder of Deeds — Open Data (Socrata).

The Socrata API is free and does not require an API key for basic usage
(rate-limited to ~1000 req/hr without a token; set `SOCRATA_APP_TOKEN` to raise that).

Fields returned per loan record:
  - document_number     : Recording instrument/document number
  - document_type       : e.g. "MORTGAGE", "RELEASE OF MORTGAGE", "DEED OF TRUST"
  - recorded_date       : Date filed with the county
  - execution_date      : Execution date (when present)
  - consideration_amount: Amount in dollars (when present)
"""

from __future__ import annotations

import os
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import httpx

from core.scraping.models import LoanRecord, PropertyRecord

if TYPE_CHECKING:
    from core.address.models import NormalizedAddress

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Primary: full/live Recorder dataset (preferred for PIN lookups)
RECORDER_DATASET_URL = "https://datacatalog.cookcountyil.gov/resource/fc9e-k9vb.json"

# Fallback: partial-year combined dataset (use only if primary returns nothing)
RECORDER_FALLBACK_DATASET_URL = "https://datacatalog.cookcountyil.gov/resource/4f2q-h3b7.json"

# Mortgage-related document type codes in the Recorder dataset
MORTGAGE_DOC_TYPES = {
    "MORTGAGE",
    "MTG",
    "DEED OF TRUST",
    "DOT",
    "HOME EQUITY",
    "HELOC",
    "CONSTRUCTION LOAN",
    "LAND TRUST MORTGAGE",
    "ASSIGNMENT OF MORTGAGE",
    "RELEASE OF MORTGAGE",
    "SATISFACTION OF MORTGAGE",
    "PARTIAL RELEASE",
    "SUBORDINATION AGREEMENT",
}

# Socrata app token — optional but increases rate limits significantly
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN", "")

REQUEST_TIMEOUT = 20  # seconds


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _normalize_pin(raw_pin: str) -> str:
    """
    Cook County PINs come in various formats:
      13-15-114-039-0000  (with dashes)
      13151140390000      (raw 14-digit)
    Normalize to the dashed display format.
    """
    digits = re.sub(r"\D", "", raw_pin)
    if len(digits) == 14:
        return f"{digits[0:2]}-{digits[2:4]}-{digits[4:7]}-{digits[7:10]}-{digits[10:14]}"
    return digits  # return as-is if unexpected length


def _build_headers() -> dict:
    headers = {"Accept": "application/json"}
    if SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = SOCRATA_APP_TOKEN
    return headers


def _parse_consideration(raw: str) -> Optional[float]:
    """Parse dollar amounts like '$250,000.00' or '250000' → float."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.]", "", raw)
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_date(raw: str) -> Optional[str]:
    """Normalize various date formats to ISO 8601 (YYYY-MM-DD)."""
    if not raw:
        return None
    # Strip trailing microseconds/timezone for ISO variants, try full string for others
    candidates = [raw, raw[:10]]  # e.g. "2021-06-15T00:00:00.000" → also try "2021-06-15"
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        for candidate in candidates:
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return raw  # return raw string as fallback


# ---------------------------------------------------------------------------
# Core scraper functions
# ---------------------------------------------------------------------------

def fetch_loans_by_pin(pin: str, limit: int = 50) -> list[LoanRecord]:
    """
    Query the Cook County Recorder of Deeds Socrata dataset by PIN.

    Args:
        pin:   14-digit Cook County Parcel Identification Number (with or without dashes).
        limit: Maximum number of records to return (default 50).

    Returns:
        List of LoanRecord objects, newest recording first.
    """
    normalized_pin = _normalize_pin(pin)
    logger.info("Fetching loan history for PIN %s", normalized_pin)

    # Socrata SoQL query — filter by PIN and mortgage-like doc types.
    # Recorder datasets use dashed PINs and the `document_type` field.
    doc_type_filter = " OR ".join(f"upper(document_type)='{dt}'" for dt in MORTGAGE_DOC_TYPES)
    params = {
        "$where": f"pin='{normalized_pin}' AND ({doc_type_filter})",
        "$order": "recorded_date DESC",
        "$limit": str(limit),
    }

    rows: list[dict] = []
    for endpoint in (RECORDER_DATASET_URL, RECORDER_FALLBACK_DATASET_URL):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                response = client.get(endpoint, params=params, headers=_build_headers())
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP error fetching recorder data (%s): %s", endpoint, exc)
            continue
        except httpx.RequestError as exc:
            logger.error("Network error fetching recorder data (%s): %s", endpoint, exc)
            continue

        if not isinstance(data, list):
            logger.warning("Unexpected response format from Recorder API (%s): %s", endpoint, type(data))
            continue
        rows = data
        if rows:
            break

    if not isinstance(rows, list):
        logger.warning("Unexpected response format from Recorder API: %s", type(rows))
        return []

    records: list[LoanRecord] = []
    for row in rows:
        record = LoanRecord(
            recorded_date=_parse_date(row.get("recorded_date") or row.get("recording_date") or ""),
            execution_date=_parse_date(row.get("execution_date") or ""),
            amount=_parse_consideration(
                str(row.get("consideration_amount") or row.get("consideration") or "")
            ),
            document_number=(
                (row.get("document_number") or row.get("instrument_number") or row.get("doc_number") or "")
                or None
            ),
            document_type=((row.get("document_type") or row.get("doc_type") or "").strip().upper() or None),
            property_address=(
                (row.get("street") or row.get("property_address") or "").strip() or None
            ),
            source_name="cook_county_recorder_socrata",
        )
        records.append(record)

    logger.info("Found %d loan record(s) for PIN %s", len(records), normalized_pin)
    return records


def fetch_loans_by_address(
    street_number: str,
    street_name: str,
    zip_code: Optional[str] = None,
    limit: int = 50,
) -> list[LoanRecord]:
    """
    Fallback: query recorder dataset by address when PIN is unavailable.

    The Recorder dataset may store address differently across entries, so this
    uses a LIKE search on the combined address fields.

    Args:
        street_number: e.g. "4406"
        street_name:   e.g. "WILSON"   (street name only, no suffix/direction)
        zip_code:      e.g. "60630"    (optional, narrows results)
        limit:         max records

    Returns:
        List of LoanRecord objects.
    """
    logger.info(
        "Fetching loan history by address: %s %s %s",
        street_number, street_name, zip_code or "",
    )

    # Address fallback is inherently less reliable because recorder address formatting varies.
    # Use `street` and `zip_code` (when present) instead of grantor_* fields.
    address_filter = f"upper(street) like '%{street_number.upper()}%{street_name.upper()}%'"
    if zip_code:
        address_filter += f" AND zip_code='{zip_code}'"

    doc_type_filter = " OR ".join(f"upper(document_type)='{dt}'" for dt in MORTGAGE_DOC_TYPES)
    params = {
        "$where": f"({address_filter}) AND ({doc_type_filter})",
        "$order": "recorded_date DESC",
        "$limit": str(limit),
    }

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(
                RECORDER_DATASET_URL,
                params=params,
                headers=_build_headers(),
            )
            response.raise_for_status()
            rows = response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("Error fetching recorder data by address: %s", exc)
        return []

    records: list[LoanRecord] = []
    for row in rows:
        record = LoanRecord(
            recorded_date=_parse_date(row.get("recorded_date") or row.get("recording_date") or ""),
            execution_date=_parse_date(row.get("execution_date") or ""),
            amount=_parse_consideration(
                str(row.get("consideration_amount") or row.get("consideration") or "")
            ),
            document_number=(
                (row.get("document_number") or row.get("instrument_number") or row.get("doc_number") or "")
                or None
            ),
            document_type=((row.get("document_type") or row.get("doc_type") or "").strip().upper() or None),
            property_address=(row.get("street") or "").strip() or None,
            source_name="cook_county_recorder_socrata",
        )
        records.append(record)

    logger.info(
        "Found %d loan record(s) by address %s %s",
        len(records), street_number, street_name,
    )
    return records


# ---------------------------------------------------------------------------
# Async wrapper for pipeline integration
# ---------------------------------------------------------------------------

async def try_fetch_clerk_loan_records(
    pin: str,
    *,
    headless: bool = True,
    address_data: Optional[dict] = None,
) -> list[LoanRecord]:
    """
    Async wrapper for the loan scraper — integrates with the existing pipeline.

    Args:
        pin:          14-digit Cook County PIN (with or without dashes)
        headless:     (unused, kept for API compatibility)
        address_data: Optional address dict for fallback lookup

    Returns:
        List of LoanRecord objects.
    """
    # Try PIN-based lookup first
    if pin:
        normalized = _normalize_pin(pin)
        if len(re.sub(r"\D", "", normalized)) == 14:
            return fetch_loans_by_pin(normalized)

    # Fall back to address-based lookup if PIN unavailable
    if address_data:
        street_number = address_data.get("street_number", "")
        street_name = address_data.get("street_name", "")
        zip_code = address_data.get("zip_code")

        if street_number and street_name:
            return fetch_loans_by_address(street_number, street_name, zip_code)

    return []


# ---------------------------------------------------------------------------
# Sync scrape function for registry-based pipeline
# ---------------------------------------------------------------------------

def scrape(address_data: dict, property_data: dict | None = None) -> list[dict]:
    """
    Primary scraping function called by the orchestration pipeline.

    Args:
        address_data:  Parsed address dict (keys: street_number, street_name,
                       zip_code, city, state, county, pipeline_id).
        property_data: Optional dict already containing `parcel_number` from a
                       prior assessor scrape. Used to look up by PIN directly.

    Returns:
        List of loan record dicts ready to be merged into the API response
        under the `loan_history` key.
    """
    # ---- 1. Try PIN-based lookup first (most accurate) --------------------
    pin: Optional[str] = None

    if property_data:
        raw_pin = property_data.get("parcel_number") or property_data.get("pin")
        if raw_pin:
            pin = _normalize_pin(str(raw_pin))

    if pin:
        records = fetch_loans_by_pin(pin)
    else:
        # ---- 2. Fall back to address-based lookup -------------------------
        street_number = address_data.get("street_number", "")
        street_name = address_data.get("street_name", "")
        zip_code = address_data.get("zip_code")

        if not street_number or not street_name:
            logger.warning("Insufficient address data for loan lookup; skipping.")
            return []

        records = fetch_loans_by_address(street_number, street_name, zip_code)

    # ---- 3. Serialize to plain dicts for the pipeline ---------------------
    return [r.model_dump(exclude_none=False) for r in records]


# ---------------------------------------------------------------------------
# Class-based scraper for pipeline integration
# ---------------------------------------------------------------------------

class CookClerkRecordingLoansScraper:
    """
    Async scraper class for Cook County Clerk recording loans.
    Follows the same pattern as other scrapers in the codebase.
    """

    def __init__(self, headless: bool = True, source_params: dict | None = None, uid: str | None = None):
        self.headless = headless
        self.source_params = source_params or {}
        self.uid = uid

    async def scrape(self, address: "NormalizedAddress") -> "PropertyRecord | None":
        """
        Scrape loan history for the given address.

        Args:
            address: NormalizedAddress object

        Returns:
            PropertyRecord with loan_history populated, or None if no data found
        """
        from core.scraping.models import PropertyRecord

        # Get PIN from address if available
        pin = getattr(address, 'parcel_number', None) if hasattr(address, 'parcel_number') else None

        # Build address_data for fallback
        address_data = {
            "street_number": address.street_number,
            "street_name": address.street_name,
            "zip_code": address.zip_code,
        }

        # Fetch loan records
        loan_records = await try_fetch_clerk_loan_records(
            pin=pin or "",
            headless=self.headless,
            address_data=address_data,
        )

        if not loan_records:
            return None

        # Create a minimal PropertyRecord with just loan_history
        record = PropertyRecord(
            loan_history=loan_records,
            source_name="us_cook_clerk_recording_loans",
            confidence=0.75,
        )

        return record

    async def close(self):
        """Close any resources (no-op for this scraper)."""
        pass
