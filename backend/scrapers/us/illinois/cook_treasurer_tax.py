"""
Cook County Treasurer — "Your Property Tax Overview" (Playwright).

Fills ``TaxRecord.total_tax``, ``total_paid``, ``total_due``, and ``last_paid`` where
the Treasurer site exposes them (typically the **current** tax cycle / recent installments).
The page layout can change; parsing uses flexible regex over visible text plus optional
tabular heuristics.

Search: https://www.cookcountytreasurer.com/yourpropertytaxoverviewsearch.aspx

The form POSTs to ``setsearchparameters.aspx`` for both the PIN step and the overview
response; that URL alone does **not** mean another PIN submit is required. The page
loads **reCAPTCHA v3** into ``GoogleCaptchaToken`` before Continue is accepted.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from bs4 import BeautifulSoup

from core.scraping.models import PropertyRecord, TaxRecord


def _with_treasurer_tax_status(record: PropertyRecord, status: str) -> PropertyRecord:
    return record.model_copy(update={"cook_treasurer_tax_status": status})

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_URL = "https://www.cookcountytreasurer.com/yourpropertytaxoverviewsearch.aspx"


@dataclass
class _TreasurerYearSnap:
    total_tax: float | None = None
    total_paid: float | None = None
    total_due: float | None = None
    last_paid: str | None = None


def _digits_pin(pin: str) -> str:
    return re.sub(r"\D", "", pin or "")


def split_cook_pin(pin: str) -> list[str] | None:
    """Cook PIN: 14 digits as 2-2-3-3-4 segments."""
    p = _digits_pin(pin)
    if len(p) != 14:
        return None
    return [p[0:2], p[2:4], p[4:7], p[7:10], p[10:14]]


def _money_from_text(s: str) -> float | None:
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _first_money_after(chunk: str, pattern: str) -> float | None:
    mm = re.search(pattern, chunk, re.IGNORECASE | re.DOTALL)
    if not mm:
        return None
    return _money_from_text(mm.group(0))


def _fill_snap_from_chunk(chunk: str) -> _TreasurerYearSnap:
    """Apply label patterns once per field (most specific first)."""
    snap = _TreasurerYearSnap()
    snap.total_tax = _first_money_after(
        chunk,
        r"(?:Total\s+Amount\s+Billed|Original\s+Tax\s+Amount|Total\s+Billed)[\s\S]{0,250}?\$?\s*[\d,]+\.?\d*",
    )
    if snap.total_tax is None:
        snap.total_tax = _first_money_after(
            chunk,
            r"(?:Billed\s+Amount|Tax\s+Billed)[\s\S]{0,250}?\$?\s*[\d,]+\.?\d*",
        )
    snap.total_paid = _first_money_after(
        chunk,
        r"(?:Total\s+Amount\s+Paid|Total\s+Paid|Amount\s+Paid)[\s\S]{0,250}?\$?\s*[\d,]+\.?\d*",
    )
    snap.total_due = _first_money_after(
        chunk,
        r"(?:Balance\s+Due|Total\s+Balance|Balance\s+Owed|Outstanding\s+Balance)[\s\S]{0,250}?\$?\s*[\d,]+\.?\d*",
    )
    if snap.total_due is None:
        snap.total_due = _first_money_after(
            chunk,
            r"(?:^|\n)\s*Balance\s*[:\s]+[\s\S]{0,120}?\$?\s*[\d,]+\.?\d*",
        )
    lp = re.search(
        r"(?:Last\s+Payment|Payment\s+Date|Date\s+Paid)\s*[:\s]*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
        chunk,
        re.IGNORECASE,
    )
    if lp:
        snap.last_paid = lp.group(1).strip()

    # Installment-style fallback (seen on Cook overview results):
    # - "Original Billed Amount: $X"
    # - "Current Amount Due: $Y"
    # We can treat total_tax as the sum of original billed amounts and total_due as
    # the sum of current amount due, then infer total_paid.
    if snap.total_due is None:
        dues = [
            _money_from_text(m.group(0))
            for m in re.finditer(r"Current\s+Amount\s+Due[\s\S]{0,80}?\$?\s*[\d,]+\.?\d*", chunk, re.I)
        ]
        dues = [d for d in dues if d is not None]
        if dues:
            snap.total_due = float(sum(dues))

    if snap.total_tax is None:
        billed = [
            _money_from_text(m.group(0))
            for m in re.finditer(r"Original\s+Billed\s+Amount[\s\S]{0,80}?\$?\s*[\d,]+\.?\d*", chunk, re.I)
        ]
        billed = [b for b in billed if b is not None]
        if billed:
            snap.total_tax = float(sum(billed))

    if snap.total_paid is None and snap.total_tax is not None and snap.total_due is not None:
        paid = snap.total_tax - snap.total_due
        # Avoid tiny negative due to rounding/parse errors
        snap.total_paid = round(paid, 2) if paid >= -0.01 else None
    return snap


def parse_treasurer_overview_html(html: str) -> dict[int, _TreasurerYearSnap]:
    """
    Extract per-year billed / paid / balance / last payment from Treasurer HTML or
    flattened text. Tuned for common wording on cookcountytreasurer.com; may return
    a subset of years (often the visible tax cycle only).
    """
    out: dict[int, _TreasurerYearSnap] = {}
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    for m in re.finditer(
        r"Tax\s*Year\s*(\d{4})([\s\S]{0,6000}?)(?=Tax\s*Year\s*\d{4}|\Z)",
        text,
        flags=re.IGNORECASE,
    ):
        year = int(m.group(1))
        chunk = m.group(2)
        snap = _fill_snap_from_chunk(chunk)
        if any((snap.total_tax, snap.total_paid, snap.total_due, snap.last_paid)):
            out[year] = snap

    if not out:
        snap = _fill_snap_from_chunk(text)
        y_hint = re.search(r"\b(20\d{2})\b(?:\s*(?:Property|Tax|Installment|Bill|Overview))", text, re.I)
        if not y_hint:
            y_hint = re.search(r"(?:Tax|Property)\s+(?:Year|Bill)\s*(\d{4})", text, re.I)
        year = int(y_hint.group(1)) if y_hint else None
        if year and any((snap.total_tax, snap.total_paid, snap.total_due, snap.last_paid)):
            out[year] = snap

    return out


def _response_has_dollar_amounts(html: str) -> bool:
    """True if HTML looks like it contains currency figures (tax bill / overview)."""
    if not html:
        return False
    return bool(re.search(r"\$\s*[\d,]{2,}", html))


def _looks_like_treasurer_shell_without_bills(html: str) -> bool:
    """
    The site often returns the public “Your Property Tax Overview” shell (PIN form + copy)
    with **no** bill rows when reCAPTCHA / bot checks fail for automated clients.
    """
    if not html or "SearchByPIN1" not in html:
        return False
    if _response_has_dollar_amounts(html):
        return False
    return "ContentPlaceHolder1_ASPxTabControl1" in html or "setsearchparameters.aspx" in html.lower()


def merge_treasurer_snaps_into_record(
    record: PropertyRecord,
    by_year: dict[int, _TreasurerYearSnap],
) -> PropertyRecord:
    """Merge Treasurer snapshots into existing ``tax_history`` rows (matched by year)."""
    if not by_year:
        return record
    years_in_hist = {tr.year for tr in record.tax_history if tr.year is not None}
    for tr in record.tax_history:
        if tr.year is None:
            continue
        snap = by_year.get(tr.year)
        if not snap:
            continue
        if snap.total_tax is not None:
            tr.total_tax = snap.total_tax
        if snap.total_paid is not None:
            tr.total_paid = snap.total_paid
        if snap.total_due is not None:
            tr.total_due = snap.total_due
        if snap.last_paid:
            tr.last_paid = snap.last_paid
    for year, snap in sorted(by_year.items(), reverse=True):
        if year in years_in_hist:
            continue
        if not any((snap.total_tax, snap.total_paid, snap.total_due, snap.last_paid)):
            continue
        record.tax_history.append(
            TaxRecord(
                year=year,
                total_tax=snap.total_tax,
                total_paid=snap.total_paid,
                total_due=snap.total_due,
                last_paid=snap.last_paid,
            )
        )
    record.tax_history.sort(key=lambda t: t.year if t.year is not None else -1, reverse=True)
    return record


async def enrich_record_with_treasurer_overview(
    record: PropertyRecord,
    *,
    headless: bool,
    search_url: str = DEFAULT_SEARCH_URL,
    timeout_ms: int = 60000,
) -> PropertyRecord:
    """
    Open the Treasurer PIN search, submit the parcel PIN, parse the overview HTML,
    and merge billed/paid/balance/last-paid into ``record.tax_history``.
    """
    pin = _digits_pin(record.parcel_number or "")
    if len(pin) != 14:
        return record
    parts = split_cook_pin(pin)
    if not parts:
        return record

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright not installed; skip Cook Treasurer tax enrichment")
        return _with_treasurer_tax_status(record, "playwright_not_installed")

    async def _fill_pin_fields(page) -> None:
        """Prefer known ASP.NET control IDs (avoids filling unrelated visible text boxes)."""
        ids = [
            "#ContentPlaceHolder1_ASPxPanel1_SearchByPIN1_txtPIN1",
            "#ContentPlaceHolder1_ASPxPanel1_SearchByPIN1_txtPIN2",
            "#ContentPlaceHolder1_ASPxPanel1_SearchByPIN1_txtPIN3",
            "#ContentPlaceHolder1_ASPxPanel1_SearchByPIN1_txtPIN4",
            "#ContentPlaceHolder1_ASPxPanel1_SearchByPIN1_txtPIN5",
        ]
        pin_ids_ok = True
        for sel in ids:
            if await page.locator(sel).count() == 0:
                pin_ids_ok = False
                break
        if pin_ids_ok:
            for sel, seg in zip(ids, parts):
                await page.locator(sel).fill(seg)
            return
        visible_text = page.locator('input[type="text"]:visible')
        n = await visible_text.count()
        if n >= 5:
            for i, seg in enumerate(parts):
                await visible_text.nth(i).fill(seg)
            return
        if n >= 1:
            await visible_text.nth(0).fill(pin)
            return
        raise RuntimeError("Cook Treasurer: no visible PIN text inputs")

    async def _wait_recaptcha_token(page, cap_ms: int) -> None:
        """PIN submit requires a populated reCAPTCHA v3 token (site sets via grecaptcha.execute)."""
        cap = max(15_000, min(55_000, cap_ms))
        try:
            await page.wait_for_function(
                """() => {
                    const el = document.getElementById('GoogleCaptchaToken');
                    return el && el.value && el.value.length > 20;
                }""",
                timeout=cap,
            )
        except Exception:
            logger.warning(
                "Cook Treasurer: GoogleCaptchaToken not ready before submit; "
                "overview may fail (blocked or slow reCAPTCHA)."
            )

    html: str | None = None
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                err = str(e)
                if "Executable doesn't exist" in err or "BrowserType.launch" in err:
                    logger.warning(
                        "Cook Treasurer: Chromium not installed (playwright install chromium). %s",
                        err[:200],
                    )
                    return _with_treasurer_tax_status(record, "chromium_not_installed")
                raise
            context = None
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    timezone_id="America/Chicago",
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                )
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)
                await page.goto(search_url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(20_000, timeout_ms))
                except Exception:
                    pass
                await _wait_recaptcha_token(page, timeout_ms)
                try:
                    await _fill_pin_fields(page)
                except Exception as e:
                    logger.warning("Cook Treasurer: PIN fill failed on %s: %s", search_url, e)
                    return _with_treasurer_tax_status(record, "treasurer_pin_form_failed")

                cont = page.locator("#ContentPlaceHolder1_ASPxPanel1_SearchByPIN1_cmdContinue")
                if await cont.count() > 0:
                    await cont.first.click()
                else:
                    subs = page.locator('input[type="submit"]:visible, button[type="submit"]:visible')
                    if await subs.count() > 0:
                        await subs.first.click()
                    else:
                        await page.get_by_role(
                            "button", name=re.compile(r"search|submit|go|continue", re.I)
                        ).first.click()
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(25_000, timeout_ms))
                except Exception:
                    pass
                # POST target is setsearchparameters.aspx for both search and results; do not
                # re-submit PIN there or a successful overview can be replaced by a blank form.
                html = await page.content()
                try:
                    logger.info(
                        "Cook Treasurer: post-submit URL=%s title=%r",
                        page.url,
                        await page.title(),
                    )
                except Exception:
                    pass
            finally:
                if context is not None:
                    await context.close()
                await browser.close()
    except Exception as e:
        logger.warning("Cook Treasurer overview scrape failed: %s: %s", type(e).__name__, e)
        return _with_treasurer_tax_status(record, "treasurer_browser_error")

    if not html:
        return _with_treasurer_tax_status(record, "treasurer_empty_html")
    by_year = parse_treasurer_overview_html(html)
    if not by_year:
        shell = _looks_like_treasurer_shell_without_bills(html)
        if shell:
            logger.warning(
                "Cook Treasurer: PIN submit returned the public shell with no tax dollar rows "
                "(common for headless or datacenter clients even when GoogleCaptchaToken is set). "
                "Try treasurer_headless: false or COOK_TREASURER_HEADLESS=false with a visible "
                "browser, run from a residential IP, or disable treasurer_tax_enrich. PIN=%s…",
                pin[:6],
            )
        else:
            logger.info(
                "Cook Treasurer: no tax bill fields parsed from overview (layout may have changed). "
                "PIN=%s… text sample: %r",
                pin[:6],
                re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ", strip=True))[:240],
            )
        return _with_treasurer_tax_status(
            record, "treasurer_shell_no_bills" if shell else "treasurer_unparsed"
        )

    merge_treasurer_snaps_into_record(record, by_year)
    logger.info("Cook Treasurer: merged tax bill fields for %d year(s)", len(by_year))
    return _with_treasurer_tax_status(record, "merged")
