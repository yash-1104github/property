"""
BS&A Online scraper for Michigan municipalities.

BS&A Online (bsaonline.com) is a JS-heavy SPA used by hundreds of Michigan
municipalities. Each municipality gets a unique `uid`. The site requires
Playwright because all search results are rendered client-side.

Navigation flow:
  1. Load homepage with ?uid={uid}
  2. Select "Address" search mode
  3. Type street name into the search field
  4. Submit and wait for results list
  5. Click matching parcel row
  6. Extract data from detail panels (Assessing + Tax tabs)
"""

import logging
import re
from datetime import datetime, timezone

from playwright.async_api import Browser, Page, Playwright, async_playwright

from core.address.models import NormalizedAddress
from core.scraping.base import BaseScraper
from core.scraping.models import (
    BuildingInfo,
    PropertyRecord,
    SaleRecord,
    TaxRecord,
)

logger = logging.getLogger(__name__)

BSA_BASE = "https://bsaonline.com"
# Refreshed BS&A (2025+) uses MunicipalityHome + unified Search box (not separate street fields).
MUNICIPALITY_HOME = f"{BSA_BASE}/Home/MunicipalityHome"
BEDFORD_TWP_UID = "995"

MUNICIPALITY_UIDS = {
    "bedford": "995",
    "bedford charter township": "995",
    "sheridan": "191",
    "sheridan township": "191",
    "marshall": "391",
    "marshall township": "391",
    "leroy": "998",
    "leroy township": "998",
}


class BSAOnlineScraper(BaseScraper):
    name = "us_michigan_bsa_online"
    requires_browser = True

    def __init__(self, uid: str = BEDFORD_TWP_UID, headless: bool = True):
        self.uid = uid
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def _get_browser(self) -> Browser:
        if self._browser is None:
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                )
            except Exception as e:
                err = str(e)
                if "Executable doesn't exist" in err or "BrowserType.launch" in err:
                    raise RuntimeError(
                        "Playwright Chromium is not installed for this Python environment. "
                        "Run once:\n\n"
                        "  python3 -m playwright install chromium\n\n"
                        "If you use a virtualenv, run the command with that env active."
                    ) from e
                raise
        return self._browser

    async def health_check(self) -> bool:
        try:
            browser = await self._get_browser()
            page = await browser.new_page()
            resp = await page.goto(f"{BSA_BASE}/?uid={self.uid}", timeout=15000)
            await page.close()
            return resp is not None and resp.ok
        except Exception:
            logger.exception("Health check failed for BSA uid=%s", self.uid)
            return False

    async def scrape(self, address: NormalizedAddress) -> PropertyRecord | None:
        browser = await self._get_browser()
        page = await browser.new_page()
        page.set_default_timeout(60000)

        try:
            return await self._do_scrape(page, address)
        except Exception:
            logger.exception(
                "Scrape failed for %s on BSA uid=%s", address.one_line, self.uid
            )
            return None
        finally:
            await page.close()

    @staticmethod
    def _search_queries(address: NormalizedAddress) -> list[str]:
        """Queries that work with BS&A unified search (try narrow → broad)."""
        qs: list[str] = []
        num = (address.street_number or "").strip()
        name = (address.street_name or "").strip()
        suf = (address.street_suffix or "").strip()
        if num and name:
            qs.append(f"{num} {name}")
            if suf:
                qs.append(f"{num} {name} {suf}")
        fs = address.full_street.strip()
        if fs and fs not in qs:
            qs.append(fs)
        # Avoid city+state in one blob — BS&A often matches worse with city included
        if address.city and num and name:
            qs.append(f"{num} {name} {address.city}")
        one = address.one_line.replace(",", " ").strip()
        if one and one not in qs:
            qs.append(one)
        seen: set[str] = set()
        out: list[str] = []
        for q in qs:
            if q and q not in seen:
                seen.add(q)
                out.append(q)
        return out

    async def _dismiss_overlays(self, page: Page):
        for name in ("Close notification",):
            try:
                btn = page.get_by_role("button", name=name)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click(timeout=2000)
            except Exception:
                pass

    async def _get_search_input(self, page: Page):
        """Unified search box on refreshed BS&A (placeholder Search)."""
        loc = page.get_by_placeholder("Search")
        if await loc.count() > 0:
            return loc.first
        loc = page.get_by_role("searchbox", name="Search")
        if await loc.count() > 0:
            return loc.first
        return page.locator('input[type="search"]').first

    async def _submit_search(self, page: Page, search_input):
        """Submit via Enter (avoids ambiguous duplicate Search buttons)."""
        await search_input.press("Enter")

    async def _has_no_results(self, page: Page) -> bool:
        try:
            loc = page.locator("text=No Records Found").first
            return await loc.is_visible()
        except Exception:
            return False

    async def _wait_for_search_results_ready(self, page: Page, timeout_ms: int = 35000) -> None:
        """BS&A loads grid data asynchronously after navigation; wait for rows or explicit empty."""
        try:
            await page.wait_for_function(
                """() => {
                  const h = [...document.querySelectorAll('h2,h3,h4,h6')];
                  if (h.some(el => (el.textContent || '').includes('Loading Data'))) return false;
                  const body = document.body.innerText || '';
                  if (body.includes('No Records Found')) return true;
                  const rows = document.querySelectorAll(
                    '.ag-body-viewport .ag-row:not(.ag-header-row), ' +
                    '.ag-center-cols-container .ag-row:not(.ag-header-row), ' +
                    'div[role="row"][row-index]:not([row-index="-1"]), ' +
                    'table tbody tr'
                  );
                  return rows.length > 0;
                }""",
                timeout=timeout_ms,
                polling=400,
            )
        except Exception:
            logger.warning("Timed out waiting for BS&A result rows to render")

    async def _prefer_list_view(self, page: Page) -> None:
        """List view often exposes stable table rows; Grid may delay row mount in headless."""
        try:
            lst = page.get_by_role("button", name="List")
            if await lst.count() == 0:
                return
            await lst.first.click(timeout=5000)
            await page.wait_for_timeout(800)
            await self._wait_for_search_results_ready(page, timeout_ms=25000)
        except Exception:
            logger.debug("Could not switch to List view")

    async def _click_first_ag_row_js(self, page: Page) -> bool:
        """Click first data row via DOM (works when Playwright locators miss virtualized grids)."""
        clicked = await page.evaluate(
            """() => {
              const sel = [
                '.ag-body-viewport .ag-row:not(.ag-header-row)',
                '.ag-center-cols-container .ag-row:not(.ag-header-row)',
                '.ag-row:not(.ag-header-row):not(.ag-row-pinned)',
              ];
              for (const s of sel) {
                const r = document.querySelector(s);
                if (r) { r.dispatchEvent(new MouseEvent('click', { bubbles: true })); return true; }
              }
              return false;
            }"""
        )
        if clicked:
            try:
                await page.wait_for_load_state("networkidle")
            except Exception:
                pass
            return True
        return False

    async def _click_property_result(self, page: Page, address: NormalizedAddress) -> bool:
        """Open first result row / card that looks like the target parcel."""
        num = (address.street_number or "").strip()
        street = (address.street_name or "").strip().upper()

        # Role-based row (often populated after List view + async load)
        if num:
            try:
                role_row = page.get_by_role("row", name=re.compile(re.escape(num), re.I))
                if await role_row.count() > 0:
                    await role_row.first.click()
                    await page.wait_for_load_state("networkidle")
                    return True
            except Exception:
                pass

        # AG-Grid (BS&A refreshed UI)
        ag_rows = page.locator(
            ".ag-body-viewport .ag-row:not(.ag-header-row), "
            ".ag-center-cols-container .ag-row:not(.ag-header-row), "
            ".ag-row:not(.ag-header-row):not(.ag-row-pinned)"
        )
        if await ag_rows.count() > 0:
            for i in range(await ag_rows.count()):
                row = ag_rows.nth(i)
                text = (await row.inner_text()).upper()
                if num and num in text and (not street or street in text):
                    await row.click(timeout=10000)
                    await page.wait_for_load_state("networkidle")
                    return True
            try:
                await ag_rows.first.click(timeout=10000)
                await page.wait_for_load_state("networkidle")
                return True
            except Exception:
                pass

        # Table layout
        rows = page.locator("main table tbody tr, table tbody tr")
        rc = await rows.count()
        for i in range(rc):
            row = rows.nth(i)
            text = (await row.inner_text()).upper()
            if num and num in text and (not street or street in text):
                link = row.locator("a").first
                if await link.count() > 0:
                    await link.click()
                    await page.wait_for_load_state("networkidle")
                    return True

        # Grid / list: property detail links (exclude search results self-links)
        links = page.locator('a[href*="/Property/"]')
        n = await links.count()
        for i in range(n):
            link = links.nth(i)
            href = (await link.get_attribute("href")) or ""
            if "PropertySearch" in href or "PropertySearchResults" in href:
                continue
            text = (await link.inner_text()).upper()
            if num and num in text and street and street in text:
                await link.click()
                await page.wait_for_load_state("networkidle")
                return True

        for i in range(n):
            link = links.nth(i)
            href = (await link.get_attribute("href")) or ""
            if "PropertySearch" in href or "PropertySearchResults" in href:
                continue
            text = (await link.inner_text()).upper()
            if num and num in text:
                await link.click()
                await page.wait_for_load_state("networkidle")
                return True

        # Last resort: first plausible property link
        for i in range(n):
            link = links.nth(i)
            href = (await link.get_attribute("href")) or ""
            if "PropertySearch" in href:
                continue
            await link.click()
            await page.wait_for_load_state("networkidle")
            return True

        if await self._click_first_ag_row_js(page):
            return True

        return False

    async def _do_scrape(self, page: Page, address: NormalizedAddress) -> PropertyRecord | None:
        url = f"{MUNICIPALITY_HOME}?uid={self.uid}"
        logger.info("Loading BSA Online: %s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle")
        await self._dismiss_overlays(page)

        # Some BS&A tenants trigger a human verification checkbox (anti-bot).
        # When this appears, we fail fast so the pipeline can try the next source.
        try:
            if await page.locator("text=Security Verification").first.is_visible():
                raise RuntimeError(
                    "BS&A blocked automation with Security Verification (checkbox). "
                    "Try another source or use proxy/stealth/human-in-the-loop."
                )
        except Exception:
            # If locator check fails, continue normally.
            pass

        try:
            await page.get_by_role("tab", name="Address").click(timeout=5000)
        except Exception:
            logger.debug("Address tab not clickable or already selected")

        search_input = await self._get_search_input(page)
        await search_input.wait_for(state="visible", timeout=30000)

        last_error: str | None = None

        for q in self._search_queries(address):
            logger.info("Trying BS&A search query: %s", q)
            await search_input.fill("")
            await search_input.fill(q)
            try:
                await self._submit_search(page, search_input)
                await page.wait_for_url("**/PropertySearch/**", timeout=60000)
                await page.wait_for_load_state("networkidle")
                await self._wait_for_search_results_ready(page)
                await self._prefer_list_view(page)
            except Exception as e:
                last_error = str(e)
                logger.warning("Search navigation failed for %r: %s", q, e)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_load_state("networkidle")
                    await self._dismiss_overlays(page)
                    search_input = await self._get_search_input(page)
                    await search_input.wait_for(state="visible", timeout=30000)
                except Exception:
                    pass
                continue

            if await self._has_no_results(page):
                logger.info("No records for query %r, trying next", q)
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_load_state("networkidle")
                await self._dismiss_overlays(page)
                search_input = await self._get_search_input(page)
                await search_input.wait_for(state="visible", timeout=30000)
                continue

            if await self._click_property_result(page, address):
                break

            logger.warning("Could not click a result for query %r", q)
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")
            search_input = await self._get_search_input(page)
            await search_input.wait_for(state="visible", timeout=30000)
        else:
            logger.warning(
                "All search queries failed for %s (last_error=%s)",
                address.one_line,
                last_error,
            )
            return None

        await page.wait_for_timeout(1500)

        # --- Extract data from the detail page ---
        record = await self._extract_detail(page, address)
        record.source_url = page.url
        record.source_name = self.name
        record.scraped_at = datetime.now(timezone.utc)

        # --- Try to grab tax tab data ---
        try:
            tax_tab = page.locator("text=Tax").first
            if await tax_tab.count() > 0:
                await tax_tab.click()
                await page.wait_for_timeout(2000)
                record.tax_history = await self._extract_tax_history(page)
        except Exception:
            logger.debug("Could not load tax tab")

        return record

    async def _extract_detail(self, page: Page, address: NormalizedAddress) -> PropertyRecord:
        """Extract property details from the BSA detail page."""
        record = PropertyRecord(property_address=address.one_line)

        body_text = await page.inner_text("body")
        html = await page.content()
        record.raw_html = html

        record.parcel_number = self._extract_field(body_text, [
            r"Parcel\s*(?:Number|ID|#)[:\s]*([A-Z0-9\-\.]+)",
            r"PIN[:\s]*([A-Z0-9\-\.]+)",
        ])

        record.owner_name = self._extract_field(body_text, [
            r"Owner\s*(?:Name)?[:\s]*([^\n]+)",
            r"Taxpayer[:\s]*([^\n]+)",
        ])

        record.owner_address = self._extract_field(body_text, [
            r"Owner\s*Address[:\s]*([^\n]+(?:\n[^\n]+)?)",
            r"Mailing\s*Address[:\s]*([^\n]+)",
        ])

        val = self._extract_field(body_text, [
            r"(?:Assessed|Assessment)\s*Value[:\s]*\$?([\d,]+)",
            r"SEV[:\s]*\$?([\d,]+)",
        ])
        if val:
            record.assessed_value = self._parse_money(val)
            record.sev = record.assessed_value

        taxable = self._extract_field(body_text, [
            r"Taxable\s*Value[:\s]*\$?([\d,]+)",
        ])
        if taxable:
            record.taxable_value = self._parse_money(taxable)

        record.property_type = self._extract_field(body_text, [
            r"(?:Property|Class|Use)\s*(?:Type|Class|Code)[:\s]*([^\n]+)",
        ])

        record.school_district = self._extract_field(body_text, [
            r"School\s*District[:\s]*([^\n]+)",
        ])

        record.legal_description = self._extract_field(body_text, [
            r"(?:Legal|Tax)\s*Description[:\s]*([^\n]+(?:\n[^\n]+)*)",
        ])

        acreage = self._extract_field(body_text, [
            r"Acreage[:\s]*([\d.]+)",
            r"Acres[:\s]*([\d.]+)",
        ])
        if acreage:
            try:
                record.acreage = float(acreage)
            except ValueError:
                pass

        record.zoning = self._extract_field(body_text, [
            r"Zoning[:\s]*([^\n]+)",
        ])

        record.building_info = self._extract_building_info(body_text)
        record.sale_history = self._extract_sales(body_text)

        record.confidence = self._compute_confidence(record)

        return record

    def _extract_field(self, text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    def _parse_money(self, value: str) -> float | None:
        try:
            return float(value.replace(",", "").replace("$", ""))
        except (ValueError, AttributeError):
            return None

    def _extract_building_info(self, text: str) -> BuildingInfo:
        info = BuildingInfo()

        yr = self._extract_field(text, [r"Year\s*Built[:\s]*(\d{4})"])
        if yr:
            info.year_built = int(yr)

        info.style = self._extract_field(text, [r"Style[:\s]*([^\n]+)"])
        info.exterior = self._extract_field(text, [r"Exterior[:\s]*([^\n]+)"])
        info.total_living_area = self._extract_field(text, [
            r"(?:Total\s*)?Living\s*Area[:\s]*([\d,]+)",
            r"Sq\.?\s*(?:Ft|Feet)[:\s]*([\d,]+)",
        ])
        info.heating_type = self._extract_field(text, [r"Heat(?:ing)?\s*(?:Type)?[:\s]*([^\n]+)"])
        info.bedrooms = self._extract_field(text, [r"Bedrooms?[:\s]*(\d+)"])
        info.bathrooms = self._extract_field(text, [
            r"Baths?[:\s]*(?:Full/Half[:\s]*)?([^\n]+)",
            r"Bathrooms?[:\s]*(\d+)",
        ])
        info.fireplace = self._extract_field(text, [r"Fireplace[:\s]*([^\n]+)"])

        return info

    def _extract_sales(self, text: str) -> list[SaleRecord]:
        records = []
        pattern = r"(\d{2}/\d{2}/\d{4})\s+\$?([\d,]+(?:\.\d{2})?)\s+([^\n]+)"
        for m in re.finditer(pattern, text):
            records.append(SaleRecord(
                date=m.group(1),
                price=self._parse_money(m.group(2)),
                buyer=m.group(3).strip() if m.group(3) else None,
            ))
        return records

    async def _extract_tax_history(self, page: Page) -> list[TaxRecord]:
        records: list[TaxRecord] = []
        body = await page.inner_text("body")

        pattern = (
            r"(\d{4})\s*[,\s]*(Summer|Winter|Annual)?\s*"
            r"\$?([\d,]+\.\d{2})\s+\$?([\d,]+\.\d{2})\s+"
            r"(\d{2}/\d{2}/\d{4})?\s*\$?([\d,]+\.\d{2})?"
        )
        for m in re.finditer(pattern, body, re.IGNORECASE):
            records.append(TaxRecord(
                year=int(m.group(1)),
                season=m.group(2),
                total_tax=self._parse_money(m.group(3)),
                total_paid=self._parse_money(m.group(4)),
                last_paid=m.group(5),
                total_due=self._parse_money(m.group(6)) if m.group(6) else 0.0,
            ))

        return records

    def _compute_confidence(self, record: PropertyRecord) -> float:
        """Simple confidence score based on field completeness."""
        score = 0.0
        total_fields = 8
        if record.parcel_number:
            score += 1
        if record.owner_name:
            score += 1
        if record.assessed_value:
            score += 1
        if record.taxable_value:
            score += 1
        if record.legal_description:
            score += 1
        if record.building_info and record.building_info.year_built:
            score += 1
        if record.tax_history:
            score += 1
        if record.sale_history:
            score += 1
        return round(score / total_fields, 2)

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
