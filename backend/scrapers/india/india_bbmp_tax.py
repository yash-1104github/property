"""
BBMP (Bruhat Bengaluru Mahanagara Palike) SAS property tax — Karnataka government portal.

Public entry: https://bbmptax.karnataka.gov.in/default.aspx

The SAS search is oriented to **PID / 10-digit application number** or **owner-name
fragments** (per on-screen help). Full street addresses may not return rows; we still
submit a condensed query from the normalized address and return whatever the portal
exposes, plus raw HTML for optional LLM enrichment.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, Playwright, async_playwright

from core.address.models import NormalizedAddress
from core.scraping.base import BaseScraper
from core.scraping.models import PropertyRecord

logger = logging.getLogger(__name__)

DEFAULT_ENTRY = "https://bbmptax.karnataka.gov.in/default.aspx"


class IndiaBbmpPropertyTaxScraper(BaseScraper):
    name = "in_india_bbmp_property_tax"
    requires_browser = True

    def __init__(
        self,
        headless: bool = True,
        uid: str | None = None,
        source_params: dict | None = None,
    ):
        _ = uid
        self.headless = headless
        params = source_params or {}
        self.entry_url = (params.get("entry_url") or DEFAULT_ENTRY).strip()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def _get_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        return self._browser

    async def health_check(self) -> bool:
        try:
            browser = await self._get_browser()
            page = await browser.new_page()
            r = await page.goto(self.entry_url, timeout=20000, wait_until="domcontentloaded")
            await page.close()
            return r is not None and r.ok
        except Exception:
            logger.exception("BBMP health check failed")
            return False

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    @staticmethod
    def _search_query(address: NormalizedAddress) -> str:
        parts = [
            address.full_street,
            address.city,
            address.zip_code,
        ]
        q = " ".join(p for p in parts if p).strip()
        if not q:
            q = address.raw_input
        return q[:120]

    async def scrape(self, address: NormalizedAddress) -> PropertyRecord | None:
        browser = await self._get_browser()
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        page.set_default_timeout(60000)
        try:
            return await self._scrape_page(page, address)
        finally:
            await context.close()

    async def _scrape_page(self, page: Page, address: NormalizedAddress) -> PropertyRecord | None:
        logger.info("BBMP: loading %s", self.entry_url)
        try:
            resp = await page.goto(
                self.entry_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            if not resp or not resp.ok:
                logger.warning("BBMP: bad HTTP status from entry page")
                return None
        except Exception as exc:
            logger.warning("BBMP: navigation failed: %s", exc)
            return None

        await self._maybe_english(page)
        await self._dismiss_noise(page)

        q = self._search_query(address)
        filled = await self._fill_primary_search(page, q)
        if not filled:
            logger.warning("BBMP: could not find a search field to fill")
            return None

        try:
            await self._submit_search(page)
        except Exception as exc:
            logger.warning("BBMP: submit failed: %s", exc)

        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(2500)

        html = await page.content()
        body = await page.inner_text("body")
        low = body.lower()
        if "no record" in low or "no data" in low or "not found" in low:
            logger.info("BBMP: portal reported no records for query")
            record = PropertyRecord(
                property_address=address.one_line,
                source_url=page.url,
                source_name=self.name,
                scraped_at=datetime.now(timezone.utc),
                raw_html=html,
                confidence=0.1,
            )
            return record

        record = self._parse_record(html, address, page.url)
        record.raw_html = html
        if not record.parcel_number and not record.owner_name:
            record.confidence = max(record.confidence, 0.15)
        return record

    async def _maybe_english(self, page: Page) -> None:
        for name in ("English",):
            try:
                link = page.get_by_role("link", name=name)
                if await link.count() > 0 and await link.first.is_visible():
                    await link.first.click(timeout=3000)
                    await page.wait_for_timeout(500)
                    return
            except Exception:
                pass

    async def _dismiss_noise(self, page: Page) -> None:
        for sel in (
            'button:has-text("Accept")',
            'button:has-text("Close")',
            'button:has-text("OK")',
        ):
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click(timeout=2000)
            except Exception:
                pass

    async def _fill_primary_search(self, page: Page, text: str) -> bool:
        candidates = [
            page.get_by_placeholder(re.compile(r"PID|Application|owner|Enter|SAS|digit", re.I)),
            page.locator('input[type="text"]'),
        ]
        for loc in candidates:
            try:
                n = await loc.count()
                for i in range(min(n, 8)):
                    el = loc.nth(i)
                    if await el.is_visible():
                        await el.click(timeout=3000)
                        await el.fill("")
                        await el.fill(text)
                        return True
            except Exception:
                continue
        return False

    async def _submit_search(self, page: Page) -> None:
        for role, name in (("button", re.compile(r"Search|Go|Submit", re.I)),):
            try:
                b = page.get_by_role(role, name=name)
                if await b.count() > 0:
                    await b.first.click(timeout=8000)
                    return
            except Exception:
                pass
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass

    def _parse_record(self, html: str, address: NormalizedAddress, url: str) -> PropertyRecord:
        soup = BeautifulSoup(html, "lxml")
        record = PropertyRecord(
            property_address=address.one_line,
            source_url=url,
            source_name=self.name,
            scraped_at=datetime.now(timezone.utc),
            confidence=0.0,
        )
        text_blocks: list[str] = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(" ", strip=True)
                value = cells[1].get_text(" ", strip=True)
                text_blocks.append(f"{label} {value}")
                self._apply_label(label, value, record)

        blob = "\n".join(text_blocks) + "\n" + soup.get_text("\n", strip=True)
        if not record.parcel_number:
            m = re.search(
                r"(?:PID|Application)\s*(?:No\.?|Number|#)?\s*[:\s]*([A-Z0-9\-/]+)",
                blob,
                re.I,
            )
            if m:
                record.parcel_number = m.group(1).strip()

        if not record.owner_name:
            m = re.search(
                r"(?:Owner|Tax\s*payer)\s*(?:Name)?\s*[:\s]*([^\n]+)",
                blob,
                re.I,
            )
            if m:
                record.owner_name = m.group(1).strip()[:200]

        for pat in (
            r"(?:Tax|Annual)\s*(?:Amount|Due)\s*[:\s]*(?:Rs\.?|INR)?\s*([\d,]+)",
            r"₹\s*([\d,]+)",
        ):
            m = re.search(pat, blob, re.I)
            if m and record.assessed_value is None:
                try:
                    record.assessed_value = float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
                break

        record.confidence = self._score(record)
        return record

    @staticmethod
    def _apply_label(label: str, value: str, record: PropertyRecord) -> None:
        l = label.lower()
        v = value.strip()
        if not v:
            return
        if "pid" in l or "application" in l:
            record.parcel_number = v
        elif "owner" in l and "address" not in l:
            record.owner_name = v[:300]
        elif "ward" in l:
            extra = f"Ward: {v}"
            record.legal_description = (
                f"{record.legal_description}; {extra}"
                if record.legal_description
                else extra
            )
        elif "zone" in l:
            record.zoning = v

    @staticmethod
    def _score(record: PropertyRecord) -> float:
        n = 0
        if record.parcel_number:
            n += 1
        if record.owner_name:
            n += 1
        if record.assessed_value:
            n += 1
        return round(min(n / 3.0, 1.0), 2)
