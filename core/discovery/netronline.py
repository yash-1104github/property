"""
NETR Online (netronline.com) — public records directory links to official county portals.

This module does not scrape NETR itself; it documents canonical directory URLs used when
building `registry/` YAML so sources align with “Go to Data Online” links from:
https://publicrecords.netronline.com/

Example (Calhoun County, MI):
https://publicrecords.netronline.com/state/MI/county/calhoun
"""

NETR_PUBLIC_RECORDS_BASE = "https://publicrecords.netronline.com"


def county_directory_url(state: str, county_slug: str) -> str:
    """Build NETR county directory URL (state two-letter, county lowercase slug)."""
    st = state.strip().upper()
    co = county_slug.strip().lower().replace(" ", "_")
    return f"{NETR_PUBLIC_RECORDS_BASE}/state/{st}/county/{co}"
