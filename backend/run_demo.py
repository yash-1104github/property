#!/usr/bin/env python3
"""
Standalone demo: scrape property tax records for
21013 DANA Drive, Battle Creek, MI 49017 (Calhoun County)

Usage (from repository root):
    python backend/run_demo.py                  # Headless mode (default)
    python backend/run_demo.py --visible        # Show browser window
    python backend/run_demo.py --no-llm         # Skip LLM enrichment

Or from the backend folder:
    cd backend && python run_demo.py

Requirements:
    cd backend && python3 -m pip install -r requirements.txt
    python3 -m playwright install chromium   # required once per machine / venv
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_ROOT.parent

sys.path.insert(0, str(_BACKEND_ROOT))
load_dotenv(_REPO_ROOT / ".env")

from core.address.normalizer import normalize_address
from core.orchestration.pipeline import run_pipeline

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("demo")

TARGET_ADDRESS = "21013 DANA Drive, Battle Creek, MI 49017"
TARGET_COUNTY = "Calhoun"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def demo_normalize():
    """Show address normalization."""
    print_section("STEP 1: Address Normalization")

    addr = normalize_address(TARGET_ADDRESS, county=TARGET_COUNTY)
    print(f"  Raw Input:       {addr.raw_input}")
    print(f"  Street Number:   {addr.street_number}")
    print(f"  Street Name:     {addr.street_name}")
    print(f"  Street Suffix:   {addr.street_suffix}")
    print(f"  City:            {addr.city}")
    print(f"  State:           {addr.state}")
    print(f"  ZIP:             {addr.zip_code}")
    print(f"  County:          {addr.county}")
    print(f"  Full Street:     {addr.full_street}")
    print(f"  Pipeline ID:     {addr.pipeline_id}")
    return addr


async def demo_scrape(headless: bool = True, use_llm: bool = True):
    """Run the full pipeline."""
    print_section("STEP 2: Scraping Property Records")
    print(f"  Target:  {TARGET_ADDRESS}")
    print(f"  County:  {TARGET_COUNTY}")
    print(f"  LLM:     {'Enabled' if use_llm else 'Disabled'}")
    print(f"  Browser: {'Headless' if headless else 'Visible'}")
    print(f"  Source Registry DB: {'Enabled' if os.getenv('USE_SITE_DATABASE','').lower() in ('1','true','yes') else 'Disabled'}")
    print()

    result = await run_pipeline(
        raw_address=TARGET_ADDRESS,
        county=TARGET_COUNTY,
        use_llm=use_llm,
        headless=headless,
    )

    print_section("RESULTS")

    if not result.success:
        print(f"  ERROR: {result.error}")
        print(f"  Duration: {result.duration_ms}ms")
        return result

    rec = result.record
    print(f"  Parcel Number:     {rec.parcel_number}")
    print(f"  Owner:             {rec.owner_name}")
    print(f"  Owner Address:     {rec.owner_address}")
    print(f"  Property Address:  {rec.property_address}")
    print(f"  Property Type:     {rec.property_type}")
    print(f"  School District:   {rec.school_district}")
    print(f"  Zoning:            {rec.zoning}")
    print()
    print(f"  Assessed Value:    ${rec.assessed_value:,.2f}" if rec.assessed_value else "  Assessed Value:    N/A")
    print(f"  Taxable Value:     ${rec.taxable_value:,.2f}" if rec.taxable_value else "  Taxable Value:     N/A")
    print(f"  SEV:               ${rec.sev:,.2f}" if rec.sev else "  SEV:               N/A")
    print(f"  Acreage:           {rec.acreage}" if rec.acreage else "  Acreage:           N/A")
    print(f"  Legal Description: {rec.legal_description}")

    if rec.building_info:
        b = rec.building_info
        print()
        print(f"  Year Built:        {b.year_built}")
        print(f"  Style:             {b.style}")
        print(f"  Living Area:       {b.total_living_area}")
        print(f"  Bedrooms:          {b.bedrooms}")
        print(f"  Bathrooms:         {b.bathrooms}")
        print(f"  Heating:           {b.heating_type}")
        print(f"  Exterior:          {b.exterior}")
        print(f"  Fireplace:         {b.fireplace}")

    if rec.tax_history:
        print()
        print("  Tax History:")
        print(f"  {'Year':<8} {'Season':<10} {'Total Tax':>12} {'Paid':>12} {'Due':>12}")
        print(f"  {'-'*54}")
        for t in rec.tax_history[:10]:
            season = t.season or ""
            tax = f"${t.total_tax:,.2f}" if t.total_tax else "N/A"
            paid = f"${t.total_paid:,.2f}" if t.total_paid else "N/A"
            due = f"${t.total_due:,.2f}" if t.total_due is not None else "N/A"
            print(f"  {t.year or '':<8} {season:<10} {tax:>12} {paid:>12} {due:>12}")

    if rec.sale_history:
        print()
        print("  Sale History:")
        for s in rec.sale_history[:5]:
            price = f"${s.price:,.2f}" if s.price else "N/A"
            print(f"    {s.date or 'N/A':<12} {price:<15} {s.buyer or ''}")

    print()
    print(f"  Source:     {rec.source_url}")
    print(f"  Confidence: {rec.confidence:.0%}")
    print(f"  Duration:   {result.duration_ms}ms")

    # Save full JSON output
    output_path = Path("output.json")
    output_data = {
        "address": result.address.model_dump(),
        "data": rec.model_dump(mode="json"),
        "success": result.success,
        "duration_ms": result.duration_ms,
    }
    output_path.write_text(json.dumps(output_data, indent=2, default=str))
    print(f"\n  Full JSON saved to: {output_path.resolve()}")

    return result


async def main():
    parser = argparse.ArgumentParser(description="Property Scraper Demo")
    parser.add_argument("--visible", action="store_true", help="Show browser window")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM enrichment")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  PROPERTY SCRAPER PROTOTYPE — DEMO")
    print("=" * 60)

    await demo_normalize()
    await demo_scrape(headless=not args.visible, use_llm=not args.no_llm)


if __name__ == "__main__":
    asyncio.run(main())
