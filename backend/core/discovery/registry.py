"""
YAML jurisdiction registry (fallback).

The scalable production approach is the Postgres site registry (3,000+ sources).
These YAML files remain useful for:
  - bootstrapping new environments
  - quick local overrides
  - emergency fallback when DB is unavailable
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.address.models import NormalizedAddress

logger = logging.getLogger(__name__)

# backend/core/discovery/registry.py → parents[2] == backend/
REGISTRY_DIR = Path(__file__).resolve().parents[2] / "registry"


@dataclass
class SourceConfig:
    name: str
    base_url: str
    scraper: str
    uid: str | None = None
    search_type: str = "address"
    requires_browser: bool = False
    data_types: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)


@dataclass
class JurisdictionEntry:
    country: str
    state: str
    county: str
    municipality: str | None = None
    sources: list[SourceConfig] = field(default_factory=list)


class JurisdictionRegistry:
    """In-memory registry of known jurisdictions and their scraper configs."""

    def __init__(self):
        self._entries: dict[str, JurisdictionEntry] = {}
        self._load_all()

    def _load_all(self):
        if not REGISTRY_DIR.exists():
            logger.warning("Registry directory not found: %s", REGISTRY_DIR)
            return

        for yaml_file in REGISTRY_DIR.rglob("*.yaml"):
            try:
                self._load_file(yaml_file)
            except Exception:
                logger.exception("Failed to load registry file: %s", yaml_file)

        logger.info("Loaded %d jurisdiction entries", len(self._entries))

    def _load_file(self, path: Path):
        with open(path) as f:
            data = yaml.safe_load(f)

        if not data or "jurisdiction" not in data:
            return

        j = data["jurisdiction"]
        key = self._make_key(j["country"], j["state"], j.get("county", ""))

        sources = []
        for src in data.get("sources", []):
            sources.append(SourceConfig(
                name=src["name"],
                base_url=src["base_url"],
                scraper=src["scraper"],
                uid=src.get("uid"),
                search_type=src.get("search_type", "address"),
                requires_browser=src.get("requires_browser", False),
                data_types=src.get("data_types", []),
                params=src.get("params", {}),
            ))

        self._entries[key] = JurisdictionEntry(
            country=j["country"],
            state=j["state"],
            county=j.get("county", ""),
            municipality=j.get("municipality"),
            sources=sources,
        )

    @staticmethod
    def _make_key(country: str, state: str, county: str) -> str:
        return f"{country}:{state}:{county}".upper()

    def lookup(self, address: NormalizedAddress) -> JurisdictionEntry | None:
        key = self._make_key(
            address.country,
            address.state or "",
            address.county or "",
        )
        entry = self._entries.get(key)
        if entry:
            return entry

        # Fallback: try without county
        fallback_key = self._make_key(address.country, address.state or "", "")
        return self._entries.get(fallback_key)

    def list_all(self) -> list[JurisdictionEntry]:
        return list(self._entries.values())
