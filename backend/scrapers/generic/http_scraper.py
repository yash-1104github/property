"""
Generic lightweight HTTP scraper for sites that don't need JS rendering.

Used as Tier 1 before escalating to Playwright. Works well for:
- Sites that return server-rendered HTML
- JSON APIs behind simple search endpoints
- PDF downloads
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from core.address.models import NormalizedAddress
from core.scraping.base import BaseScraper
from core.scraping.models import PropertyRecord

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class GenericHTTPScraper(BaseScraper):
    """Stateless HTTP scraper for simple, server-rendered property sites."""

    name = "generic_http"
    requires_browser = False

    def __init__(self, search_url: str, method: str = "GET", timeout: float = 15.0):
        self.search_url = search_url
        self.method = method
        self.timeout = timeout

    async def health_check(self) -> bool:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            try:
                r = await client.head(self.search_url, follow_redirects=True)
                return r.status_code < 400
            except httpx.HTTPError:
                return False

    async def scrape(self, address: NormalizedAddress) -> PropertyRecord | None:
        params = {
            "address": address.full_street,
            "city": address.city or "",
            "zip": address.zip_code or "",
        }

        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
        ) as client:
            if self.method == "POST":
                resp = await client.post(self.search_url, data=params)
            else:
                resp = await client.get(self.search_url, params=params)

            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        record = PropertyRecord(
            property_address=address.one_line,
            source_url=str(resp.url),
            source_name=self.name,
            scraped_at=datetime.now(timezone.utc),
            raw_html=resp.text,
        )

        self._extract_from_soup(soup, record)
        return record

    def _extract_from_soup(self, soup: BeautifulSoup, record: PropertyRecord):
        """Override in subclasses for site-specific extraction."""
        tables = soup.find_all("table")
        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    value = cells[1].get_text(strip=True)
                    self._map_field(label, value, record)

    @staticmethod
    def _map_field(label: str, value: str, record: PropertyRecord):
        label_lower = label.lower()
        if "parcel" in label_lower or "pin" in label_lower:
            record.parcel_number = value
        elif "owner" in label_lower and "address" not in label_lower:
            record.owner_name = value
        elif "assessed" in label_lower or "sev" in label_lower:
            try:
                record.assessed_value = float(value.replace(",", "").replace("$", ""))
            except ValueError:
                pass
        elif "taxable" in label_lower:
            try:
                record.taxable_value = float(value.replace(",", "").replace("$", ""))
            except ValueError:
                pass
        elif "acreage" in label_lower or "acres" in label_lower:
            try:
                record.acreage = float(value.replace(",", ""))
            except ValueError:
                pass
