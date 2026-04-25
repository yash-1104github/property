"""
Main orchestration pipeline.

Given a raw address string, this module:
  1. Normalizes the address
  2. Resolves ordered sources (DB registry preferred; YAML fallback)
  3. Iterates sources until one returns a record
  4. Optionally enriches with LLM extraction
  5. Returns a structured PropertyRecord
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from core.address.models import NormalizedAddress
from core.address.normalizer import normalize_address
from core.discovery.source_resolver import resolve_ordered_sources
from core.extraction.llm_extractor import extract_with_llm, merge_llm_into_record
from core.scraping.models import PropertyRecord
from scrapers.us.illinois.cook_assessor_parcel_addresses import CookAssessorParcelAddressesScraper
from scrapers.us.illinois.cook_clerk_recording_loans import try_fetch_clerk_loan_records
from scrapers.us.illinois.cook_clerk_recording_loans import CookClerkRecordingLoansScraper
from scrapers.us.michigan.arcgis_parcel_query import ArcGISParcelQueryScraper
from scrapers.us.michigan.bsa_online import BSAOnlineScraper
from scrapers.us.regrid_parcel import RegridParcelScraper, regrid_path_for_address

logger = logging.getLogger(__name__)


class ScrapeResult:
    def __init__(
        self,
        address: NormalizedAddress,
        record: PropertyRecord | None = None,
        error: str | None = None,
        duration_ms: int = 0,
    ):
        self.address = address
        self.record = record
        self.error = error
        self.duration_ms = duration_ms

    @property
    def success(self) -> bool:
        return self.record is not None and self.error is None


SCRAPER_MAP = {
    "us_arcgis_parcel_query": ArcGISParcelQueryScraper,
    "us_cook_assessor_parcel_addresses": CookAssessorParcelAddressesScraper,
    "us_cook_clerk_recording_loans": CookClerkRecordingLoansScraper,
    "us_michigan_bsa_online": BSAOnlineScraper,
    "us_regrid_parcel": RegridParcelScraper,
}


async def run_pipeline(
    raw_address: str,
    county: str | None = None,
    use_llm: bool = True,
    headless: bool = True,
    include_loan_history: bool = False,
) -> ScrapeResult:
    """End-to-end pipeline: US address in → property data out."""
    start = datetime.now(timezone.utc)

    address = normalize_address(raw_address, county=county)
    logger.info("Normalized: %s → %s (pipeline: %s)", raw_address, address.one_line, address.pipeline_id)

    # Step 2: Resolve ordered sources (PostgreSQL site DB when USE_SITE_DATABASE=true, else YAML registry)
    sources = resolve_ordered_sources(address)

    record = None
    failures: list[str] = []

    if sources:
        for source in sources:
            scraper_cls = SCRAPER_MAP.get(source.scraper)
            if not scraper_cls:
                failures.append(f"{source.name}: scraper {source.scraper!r} is not implemented in SCRAPER_MAP")
                logger.debug("Skipping unimplemented scraper: %s (%s)", source.scraper, source.name)
                continue
            kwargs: dict = {
                "headless": headless,
                "source_params": source.params or {},
            }
            if source.uid:
                kwargs["uid"] = source.uid
            scraper = scraper_cls(**kwargs)
            logger.info(
                "Trying source: %s (scraper=%s uid=%s)",
                source.name,
                source.scraper,
                source.uid,
            )
            try:
                record = await scraper.scrape(address)
            except Exception as exc:
                msg = f"{source.name}: {type(exc).__name__}: {exc}"
                logger.warning("Source failed: %s", msg)
                failures.append(msg)
                record = None
            else:
                if record is None:
                    failures.append(f"{source.name}: no matching parcel data returned")
            finally:
                await scraper.close()

            if record is not None:
                break

    # Step 3b: If no sale_history from primary sources, try Regrid as fallback
    if record and not record.sale_history and os.getenv("REGRID_API_TOKEN"):
        logger.info("No sale history from primary sources, trying Regrid fallback")
        regrid_scraper = RegridParcelScraper(
            headless=headless,
            source_params={"regrid_path": regrid_path_for_address(address)},
        )
        try:
            regrid_record = await regrid_scraper.scrape(address)
            if regrid_record and regrid_record.sale_history:
                logger.info("Regrid provided %d sale records", len(regrid_record.sale_history))
                # Merge sale history (keep existing, append Regrid's)
                existing_docs = {(s.date, s.document_number) for s in record.sale_history}
                for sale in regrid_record.sale_history:
                    key = (sale.date, sale.document_number)
                    if key not in existing_docs:
                        record.sale_history.append(sale)
                record.sale_history.sort(key=lambda x: x.date or "", reverse=True)
                # Update confidence if we got new data
                record.confidence = min(record.confidence + 0.05, 1.0)
        except Exception as exc:
            logger.warning("Regrid fallback failed: %s", exc)
        finally:
            await regrid_scraper.close()

    # Step 3c: Try to fetch loan history for Cook County IL
    if (
        include_loan_history
        and record
        and record.parcel_number
        and address.state == "IL"
        and address.county == "Cook"
    ):
        logger.info("Attempting to fetch loan history for PIN: %s", record.parcel_number)
        try:
            # Build address_data for fallback lookup
            address_data = {
                "street_number": address.street_number,
                "street_name": address.street_name,
                "zip_code": address.zip_code,
            }
            loan_records = await try_fetch_clerk_loan_records(
                record.parcel_number,
                headless=headless,
                address_data=address_data,
            )
            if loan_records:
                record.loan_history.extend(loan_records)
                logger.info("Added %d loan records from Cook Clerk", len(loan_records))
                record.confidence = min(record.confidence + 0.05, 1.0)
            else:
                logger.info("No loan records returned from Cook Clerk (portal may be blocked)")
        except Exception as exc:
            logger.warning("Loan history fetch failed: %s: %s", type(exc).__name__, exc)

    # No configured sources for this jurisdiction — optional Michigan BS&A default
    if record is None and address.state == "MI" and not sources:
        logger.info("No registry entry — default BS&A scraper")
        scraper = BSAOnlineScraper(headless=headless)
        try:
            record = await scraper.scrape(address)
        except Exception as exc:
            logger.exception("Default BS&A scraper failed")
            failures.append(f"BS&A (default): {type(exc).__name__}: {exc}")
            record = None
        else:
            if record is None:
                failures.append("BS&A (default): no matching parcel data returned")
        finally:
            await scraper.close()

    if record is None and not sources and address.state != "MI":
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        return ScrapeResult(
            address=address,
            error=(
                f"No data source configured for this US jurisdiction: {address.pipeline_id}. "
                "Add a registry entry (YAML/DB) or implement the scraper."
            ),
            duration_ms=elapsed,
        )

    if record is None:
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        if failures:
            msg = "All sources failed:\n" + "\n".join(failures)
        else:
            msg = (
                "All sources returned no data — address may not exist on the target sites, "
                "or every source was skipped (unimplemented scraper)."
            )
        return ScrapeResult(address=address, error=msg, duration_ms=elapsed)

    # Step 4: LLM enrichment via Gemini (if enabled and API key available)
    if use_llm and os.getenv("GEMINI_API_KEY") and record.raw_html:
        try:
            llm_data = await extract_with_llm(record.raw_html)
            if llm_data:
                record = merge_llm_into_record(record, llm_data)
                record.confidence = min(record.confidence + 0.15, 1.0)
                logger.info("LLM enrichment applied, confidence now %.2f", record.confidence)
        except Exception:
            logger.exception("LLM extraction failed, continuing with regex data")

    # Don't return the full HTML in the API response
    record.raw_html = None

    elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    return ScrapeResult(address=address, record=record, duration_ms=elapsed)
