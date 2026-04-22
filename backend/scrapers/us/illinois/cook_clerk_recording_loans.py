"""
Cook County Clerk — recorded instruments (best-effort).

The public recording portal (``ccrd.cookcountyclerkil.gov``) is often protected by
Cloudflare and may **block datacenter / headless traffic**. This module only runs
when explicitly enabled; it returns an empty list when access is denied.

Enable with env ``COOK_CLERK_RECORDING_SCRAPE=true`` or registry param
``clerk_loan_scrape: true`` on the Cook assessor source.

When access succeeds, future versions can parse PIN search results into
``LoanRecord`` rows; today we detect blocks and no-op so the pipeline stays stable.
"""

from __future__ import annotations

import logging
import re

from core.scraping.models import LoanRecord

logger = logging.getLogger(__name__)

CLERK_HOME = "https://ccrd.cookcountyclerkil.gov/i2/default.aspx"


async def try_fetch_clerk_loan_records(pin: str, *, headless: bool) -> list[LoanRecord]:
    """Return loan/mortgage rows from the Clerk portal when reachable; else []."""
    pin = re.sub(r"\D", "", pin or "")
    if len(pin) != 14:
        return []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Cook Clerk: playwright not installed; skip")
        return []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/Chicago",
            )
            page = await context.new_page()
            page.set_default_timeout(45_000)
            try:
                await page.goto(CLERK_HOME, wait_until="domcontentloaded")
                title = (await page.title()).lower()
                html = (await page.content()).lower()
                if "cloudflare" in title or "access denied" in title or "cloudflare" in html:
                    logger.warning(
                        "Cook Clerk recording portal blocked (Cloudflare / access denied). "
                        "PIN=%s… — run from a residential IP or disable COOK_CLERK_RECORDING_SCRAPE.",
                        pin[:6],
                    )
                    return []
                # Placeholder: PIN search UI varies; do not guess-post without a verified flow.
                logger.info(
                    "Cook Clerk portal reachable but PIN search parsing is not implemented; PIN=%s…",
                    pin[:6],
                )
                return []
            finally:
                await context.close()
                await browser.close()
    except Exception as e:
        logger.warning("Cook Clerk recording probe failed: %s: %s", type(e).__name__, e)
        return []
