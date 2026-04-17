"""
Resolve an ordered list of sources for an address.

Primary: PostgreSQL site registry (3,000+ portals) when USE_SITE_DATABASE=true.
Fallback: YAML files under `registry/` (optional once DB coverage is complete).

Execution model (controlled fallback, not infinite crawling):
  for source in ordered_sources:
      result = scrape(source)
      if result: return result
  return failure
"""

from __future__ import annotations

import logging
import os

from core.address.models import NormalizedAddress
from core.discovery.registry import JurisdictionRegistry, SourceConfig
from core.discovery.site_repository import fetch_sources_for_address

logger = logging.getLogger(__name__)


def resolve_ordered_sources(address: NormalizedAddress) -> list[SourceConfig]:
    """
    Prefer PostgreSQL site registry when USE_SITE_DATABASE=true.
    Otherwise use YAML files under registry/.
    Optionally merge YAML after DB when MERGE_YAML_REGISTRY=true (same scraper deduped by name+uid).
    """
    out: list[SourceConfig] = []

    db_sources = fetch_sources_for_address(address)
    if db_sources:
        out.extend(db_sources)

    merge_yaml = os.getenv("MERGE_YAML_REGISTRY", "").lower() in ("1", "true", "yes")

    # If Postgres returned rows but none map to a scraper we actually ship, merge YAML
    # so Calhoun BS&A (uid 662) still runs instead of only e.g. FetchGIS placeholders.
    def _has_implemented_scraper(sources: list[SourceConfig]) -> bool:
        from core.orchestration.pipeline import SCRAPER_MAP

        return any(bool(s.scraper and SCRAPER_MAP.get(s.scraper)) for s in sources)

    need_yaml = not out or merge_yaml or not _has_implemented_scraper(out)
    if need_yaml:
        reg = JurisdictionRegistry()
        entry = reg.lookup(address)
        if entry and entry.sources:
            for s in entry.sources:
                if _is_duplicate(out, s):
                    continue
                out.append(s)
            if not _has_implemented_scraper(out) and entry.sources:
                logger.warning(
                    "YAML registry has no implemented scrapers for %s",
                    address.pipeline_id,
                )

    # Prefer sources that explicitly provide tax data first
    out.sort(key=lambda s: 0 if "tax" in (s.data_types or []) else 1)
    return out


def _is_duplicate(existing: list[SourceConfig], candidate: SourceConfig) -> bool:
    for e in existing:
        if e.name == candidate.name and e.uid == candidate.uid and e.scraper == candidate.scraper:
            return True
    return False
