"""
Main orchestration pipeline.

Given a raw address string, this module:
  1. Normalizes the address
  2. Resolves ordered sources (DB registry preferred; YAML fallback)
  3. Iterates sources until one returns a record
  4. Optionally enriches with LLM extraction
  5. Returns a structured PropertyRecord
"""

import logging
import os
from datetime import datetime, timezone

from core.address.models import NormalizedAddress
from core.address.normalizer import normalize_address
from core.discovery.source_resolver import resolve_ordered_sources
from core.extraction.llm_extractor import extract_with_llm, merge_llm_into_record
from core.scraping.models import PropertyRecord
from scrapers.us.illinois.cook_assessor_parcel_addresses import CookAssessorParcelAddressesScraper
from scrapers.us.michigan.arcgis_parcel_query import ArcGISParcelQueryScraper
from scrapers.us.michigan.bsa_online import BSAOnlineScraper
from scrapers.us.regrid_parcel import RegridParcelScraper

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
    "us_michigan_bsa_online": BSAOnlineScraper,
    "us_regrid_parcel": RegridParcelScraper,
}


async def run_pipeline(
    raw_address: str,
    county: str | None = None,
    use_llm: bool = True,
    headless: bool = True,
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
