from abc import ABC, abstractmethod

from core.address.models import NormalizedAddress
from core.scraping.models import PropertyRecord


class BaseScraper(ABC):
    """Base class for all property data scrapers."""

    name: str = "base"
    requires_browser: bool = False

    @abstractmethod
    async def scrape(self, address: NormalizedAddress) -> PropertyRecord | None:
        """Scrape property data for the given address."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the target site is reachable."""
        ...
