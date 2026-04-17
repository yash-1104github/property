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
from urllib.parse import quote_plus

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
                    args=["--disable-blink-features=AutomationControlled"],
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
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1365, "height": 900},
            locale="en-US",
            timezone_id="America/Detroit",
        )
        page = await context.new_page()
        page.set_default_timeout(60000)

        try:
            return await self._do_scrape(page, address)
        except Exception:
            logger.exception(
                "Scrape failed for %s on BSA uid=%s", address.one_line, self.uid
            )
            return None
        finally:
            await context.close()

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

    @staticmethod
    def _is_non_detail_href(href: str) -> bool:
        """BS&A wraps some navigation in PropertySearch URLs; avoid treating those as parcel links."""
        if not href:
            return True
        if "PropertySearchResults" in href:
            return True
        if "SearchText=" in href:
            return True
        return False

    async def _wait_for_search_results_ready(
        self,
        page: Page,
        address: NormalizedAddress | None = None,
        timeout_ms: int = 35000,
    ) -> None:
        """Wait for async results: grid/table, property links, or card-style 'Showing … items' lists."""
        num = (address.street_number or "").strip() if address else ""
        st = (address.street_name or "").strip().upper() if address else ""
        try:
            await page.wait_for_function(
                """(tokens) => {
                  const num = (tokens[0] || '').trim();
                  const street = (tokens[1] || '').toUpperCase();
                  const body = document.body.innerText || '';
                  const loading = [...document.querySelectorAll('h2,h3,h4,h6')].some(
                    el => (el.textContent || '').includes('Loading Data'));
                  if (loading) return false;
                  if (body.includes('No Records Found')) return true;
                  const rows = document.querySelectorAll(
                    '.ag-body-viewport .ag-row:not(.ag-header-row), ' +
                    '.ag-center-cols-container .ag-row:not(.ag-header-row), ' +
                    'div[role="row"][row-index]:not([row-index="-1"]), ' +
                    'table tbody tr'
                  );
                  if (rows.length > 0) return true;
                  const propLinks = [...document.querySelectorAll('a[href*="/Property"]')].filter(a => {
                    const h = a.getAttribute('href') || '';
                    if (h.includes('PropertySearchResults')) return false;
                    if (h.includes('SearchText=')) return false;
                    return h.includes('/Property');
                  });
                  if (propLinks.length > 0) return true;
                  if (num && body.includes(num)) {
                    if (street && body.toUpperCase().includes(street)) {
                      if (/Showing\\s+\\d+\\s*-\\s*\\d+\\s+of\\s+\\d+/i.test(body)) return true;
                      if (body.includes('Owned By') || body.includes('Parcel')) return true;
                    }
                  }
                  return false;
                }""",
                arg=[num, st],
                timeout=timeout_ms,
                polling=400,
            )
        except Exception:
            logger.warning("Timed out waiting for BS&A result rows to render")

    async def _prefer_list_view(self, page: Page, address: NormalizedAddress | None = None) -> None:
        """List view often exposes stable table rows; Grid may delay row mount in headless."""
        try:
            lst = page.get_by_role("button", name="List")
            if await lst.count() == 0:
                return
            await lst.first.click(timeout=5000)
            await page.wait_for_timeout(800)
            await self._wait_for_search_results_ready(page, address, timeout_ms=25000)
        except Exception:
            logger.debug("Could not switch to List view")

    async def _soft_wait_after_navigation(self, page: Page, timeout_ms: int = 25000) -> None:
        """BS&A is SPA-heavy; avoid unbounded networkidle waits that can exceed 60s."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=min(15000, timeout_ms))
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=min(12000, timeout_ms))
        except Exception:
            pass

    async def _security_verification_blocks(self, page: Page) -> bool:
        try:
            loc = page.get_by_text("Security Verification", exact=False)
            return await loc.first.is_visible(timeout=2500)
        except Exception:
            return False

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
            await self._soft_wait_after_navigation(page)
            return True
        return False

    async def _click_result_card_js(self, page: Page, address: NormalizedAddress) -> bool:
        """DOM click for card-style results where Playwright locators miss the hit target."""
        num = (address.street_number or "").strip()
        street = (address.street_name or "").strip().upper()
        if not num:
            return False
        clicked = await page.evaluate(
            """([num, street]) => {
              const n = String(num);
              const s = String(street || '').toUpperCase();
              const matches = (t) => {
                const u = (t || '').toUpperCase();
                if (!u.includes(n)) return false;
                if (s && !u.includes(s)) return false;
                return true;
              };
              const bad = (h) => {
                if (!h) return true;
                if (h.includes('PropertySearchResults')) return true;
                if (h.includes('SearchText=')) return true;
                return false;
              };
              const links = [...document.querySelectorAll('a[href]')];
              for (const a of links) {
                const href = a.getAttribute('href') || '';
                if (bad(href)) continue;
                if (!href.includes('/Property')) continue;
                if (matches(a.innerText || '')) { a.click(); return true; }
              }
              for (const a of links) {
                const href = a.getAttribute('href') || '';
                if (bad(href)) continue;
                if (!href.includes('/Property')) continue;
                a.click();
                return true;
              }
              for (const el of document.querySelectorAll('a, button, [role="button"], div[tabindex="0"]')) {
                const t = el.innerText || el.textContent || '';
                if (t.length > 200) continue;
                if (!matches(t)) continue;
                const u = t.toUpperCase();
                if (u.includes('FILTER') || u.includes('SEARCH') || u.includes('LIST') || u.includes('GRID'))
                  continue;
                el.click();
                return true;
              }
              return false;
            }""",
            [num, street],
        )
        if clicked:
            await self._soft_wait_after_navigation(page)
            return True
        return False

    async def _click_property_result(self, page: Page, address: NormalizedAddress) -> bool:
        """Open first result row / card that looks like the target parcel."""
        num = (address.street_number or "").strip()
        street = (address.street_name or "").strip().upper()

        if await self._click_result_card_js(page, address):
            return True

        # Anchors to property detail (Calhoun / refreshed BS&A card list)
        if num:
            detail_links = page.locator('a[href*="/Property/"]').filter(
                has_text=re.compile(re.escape(num))
            )
            n_dl = await detail_links.count()
            for i in range(n_dl):
                link = detail_links.nth(i)
                href = (await link.get_attribute("href")) or ""
                if self._is_non_detail_href(href):
                    continue
                text = (await link.inner_text()).upper()
                if street and street not in text:
                    continue
                try:
                    await link.click(timeout=15000)
                    await self._soft_wait_after_navigation(page)
                    return True
                except Exception:
                    continue
            for i in range(n_dl):
                link = detail_links.nth(i)
                href = (await link.get_attribute("href")) or ""
                if self._is_non_detail_href(href):
                    continue
                try:
                    await link.click(timeout=15000)
                    await self._soft_wait_after_navigation(page)
                    return True
                except Exception:
                    continue

        # Clickable card / row containing street number (non-link wrappers)
        if num and street:
            try:
                row = page.get_by_text(re.compile(rf"{re.escape(num)}\s+{re.escape(street)}", re.I)).first
                if await row.count() > 0:
                    await row.click(timeout=15000)
                    await self._soft_wait_after_navigation(page)
                    return True
            except Exception:
                pass

        # Role-based row (often populated after List view + async load)
        if num:
            try:
                role_row = page.get_by_role("row", name=re.compile(re.escape(num), re.I))
                if await role_row.count() > 0:
                    await role_row.first.click()
                    await self._soft_wait_after_navigation(page)
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
                    await self._soft_wait_after_navigation(page)
                    return True
            try:
                await ag_rows.first.click(timeout=10000)
                await self._soft_wait_after_navigation(page)
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
                    await self._soft_wait_after_navigation(page)
                    return True

        # Grid / list: property detail links (exclude search results self-links)
        links = page.locator('a[href*="/Property/"]')
        n = await links.count()
        for i in range(n):
            link = links.nth(i)
            href = (await link.get_attribute("href")) or ""
            if self._is_non_detail_href(href):
                continue
            text = (await link.inner_text()).upper()
            if num and num in text and street and street in text:
                await link.click()
                await self._soft_wait_after_navigation(page)
                return True

        for i in range(n):
            link = links.nth(i)
            href = (await link.get_attribute("href")) or ""
            if self._is_non_detail_href(href):
                continue
            text = (await link.inner_text()).upper()
            if num and num in text:
                await link.click()
                await self._soft_wait_after_navigation(page)
                return True

        # Last resort: first plausible property link
        for i in range(n):
            link = links.nth(i)
            href = (await link.get_attribute("href")) or ""
            if self._is_non_detail_href(href):
                continue
            await link.click()
            await self._soft_wait_after_navigation(page)
            return True

        if await self._click_first_ag_row_js(page):
            return True

        return False

    async def _raise_if_security_wall(self, page: Page) -> None:
        if await self._security_verification_blocks(page):
            raise RuntimeError(
                "BS&A blocked automation with Security Verification (checkbox). "
                "Try another source or use proxy/stealth/human-in-the-loop."
            )

    async def _scrape_via_direct_property_search(self, page: Page, address: NormalizedAddress) -> bool:
        """
        Open the same URL shape the site uses after an address search, e.g.:
        /PropertySearch/PropertySearchResults?SearchType=1&uid=662&SearchText=...
        This is more reliable than waiting for SPA navigation from MunicipalityHome.
        """
        for q in self._search_queries(address):
            st = quote_plus(q)
            direct = (
                f"{BSA_BASE}/PropertySearch/PropertySearchResults"
                f"?SearchType=1&uid={self.uid}&SearchText={st}"
            )
            logger.info("Trying BS&A PropertySearchResults URL (uid=%s)", self.uid)
            try:
                await page.goto(direct, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                logger.warning("PropertySearchResults goto failed for %r: %s", q, exc)
                continue
            await self._soft_wait_after_navigation(page)
            await self._dismiss_overlays(page)
            await self._raise_if_security_wall(page)
            await self._wait_for_search_results_ready(page, address, timeout_ms=45000)
            if await self._has_no_results(page):
                logger.info("No records (direct URL) for query %r", q)
                continue
            await self._prefer_list_view(page, address)
            if await self._click_property_result(page, address):
                return True
        return False

    async def _scrape_via_municipality_home(
        self, page: Page, address: NormalizedAddress, home_url: str
    ) -> bool:
        """Legacy path: MunicipalityHome → Address tab → unified search → results."""
        logger.info("Loading BSA MunicipalityHome: %s", home_url)
        await page.goto(home_url, wait_until="domcontentloaded", timeout=60000)
        await self._soft_wait_after_navigation(page)
        await self._dismiss_overlays(page)
        await self._raise_if_security_wall(page)

        try:
            await page.get_by_role("tab", name="Address").click(timeout=5000)
        except Exception:
            logger.debug("Address tab not clickable or already selected")

        search_input = await self._get_search_input(page)
        await search_input.wait_for(state="visible", timeout=30000)

        last_error: str | None = None

        for q in self._search_queries(address):
            logger.info("Trying BS&A search query (home flow): %s", q)
            await search_input.fill("")
            await search_input.fill(q)
            try:
                await self._submit_search(page, search_input)
                # Navigation may be soft / slow; do not block only on URL.
                try:
                    await page.wait_for_url("**/PropertySearch**", timeout=25000)
                except Exception:
                    pass
                await self._soft_wait_after_navigation(page)
                await self._wait_for_search_results_ready(page, address, timeout_ms=45000)
                await self._prefer_list_view(page, address)
            except Exception as e:
                last_error = str(e)
                logger.warning("Search navigation failed for %r: %s", q, e)
                try:
                    await page.goto(home_url, wait_until="domcontentloaded", timeout=60000)
                    await self._soft_wait_after_navigation(page)
                    await self._dismiss_overlays(page)
                    search_input = await self._get_search_input(page)
                    await search_input.wait_for(state="visible", timeout=30000)
                except Exception:
                    pass
                continue

            if await self._has_no_results(page):
                logger.info("No records for query %r, trying next", q)
                await page.goto(home_url, wait_until="domcontentloaded")
                await self._soft_wait_after_navigation(page)
                await self._dismiss_overlays(page)
                search_input = await self._get_search_input(page)
                await search_input.wait_for(state="visible", timeout=30000)
                continue

            if await self._click_property_result(page, address):
                return True

            logger.warning("Could not click a result for query %r", q)
            await page.goto(home_url, wait_until="domcontentloaded")
            await self._soft_wait_after_navigation(page)
            search_input = await self._get_search_input(page)
            await search_input.wait_for(state="visible", timeout=30000)

        logger.warning(
            "MunicipalityHome flow failed for %s (last_error=%s)",
            address.one_line,
            last_error,
        )
        return False

    async def _finalize_property_detail(self, page: Page, address: NormalizedAddress) -> PropertyRecord:
        await page.wait_for_timeout(1500)

        record = await self._extract_detail(page, address)
        record.source_url = page.url
        record.source_name = self.name
        record.scraped_at = datetime.now(timezone.utc)

        try:
            tax_tab = page.locator("text=Tax").first
            if await tax_tab.count() > 0:
                await tax_tab.click()
                await page.wait_for_timeout(2000)
                record.tax_history = await self._extract_tax_history(page)
        except Exception:
            logger.debug("Could not load tax tab")

        return record

    async def _do_scrape(self, page: Page, address: NormalizedAddress) -> PropertyRecord | None:
        home = f"{MUNICIPALITY_HOME}?uid={self.uid}"

        try:
            if await self._scrape_via_direct_property_search(page, address):
                return await self._finalize_property_detail(page, address)
        except RuntimeError:
            raise

        try:
            if await self._scrape_via_municipality_home(page, address, home):
                return await self._finalize_property_detail(page, address)
        except RuntimeError:
            raise

        return None

    async def _extract_detail(self, page: Page, address: NormalizedAddress) -> PropertyRecord:
        """Extract property details from the BSA detail page."""
        record = PropertyRecord(property_address=address.one_line)

        body_text = await page.inner_text("body")
        html = await page.content()
        record.raw_html = html

        record.parcel_number = self._extract_field(body_text, [
            r"Parcel\s*#\s*([A-Z0-9\-\.]+)",
            r"Parcel\s*(?:Number|ID|#)[:\s]*([A-Z0-9\-\.]+)",
            r"PIN[:\s]*([A-Z0-9\-\.]+)",
        ])

        record.owner_name = self._extract_field(body_text, [
            r"Owned\s+By\s+([^\n]+)",
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
