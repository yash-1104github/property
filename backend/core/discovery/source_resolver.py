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
    if not out or merge_yaml:
        reg = JurisdictionRegistry()
        entry = reg.lookup(address)
        if entry and entry.sources:
            if merge_yaml or not out:
                for s in entry.sources:
                    if _is_duplicate(out, s):
                        continue
                    out.append(s)

    # Prefer sources that explicitly provide tax data first
    out.sort(key=lambda s: 0 if "tax" in (s.data_types or []) else 1)
    return out


def _is_duplicate(existing: list[SourceConfig], candidate: SourceConfig) -> bool:
    for e in existing:
        if e.name == candidate.name and e.uid == candidate.uid and e.scraper == candidate.scraper:
            return True
    return False
