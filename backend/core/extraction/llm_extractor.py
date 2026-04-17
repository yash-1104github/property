"""
LLM-powered property data extraction using Google Gemini.

When regex-based scraping fails or returns incomplete data, we feed the
raw HTML (stripped of boilerplate) to Gemini and ask it to extract structured
property fields. This works across languages, layouts, and naming conventions.
"""

import json
import logging
import os
import re

from bs4 import BeautifulSoup

from core.scraping.models import BuildingInfo, PropertyRecord, SaleRecord, TaxRecord

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are a property data extraction assistant. Given raw text from a property records website, extract the following fields into valid JSON. If a field is not present in the text, use null.

Return ONLY valid JSON with this exact structure:
{
  "parcel_number": "string or null",
  "owner_name": "string or null",
  "owner_address": "string or null",
  "property_address": "string or null",
  "property_type": "string or null",
  "school_district": "string or null",
  "zoning": "string or null",
  "assessed_value": "number or null",
  "taxable_value": "number or null",
  "sev": "number or null",
  "acreage": "number or null",
  "legal_description": "string or null",
  "building_info": {
    "year_built": "number or null",
    "style": "string or null",
    "exterior": "string or null",
    "total_living_area": "string or null",
    "heating_type": "string or null",
    "bedrooms": "string or null",
    "bathrooms": "string or null",
    "fireplace": "string or null"
  },
  "tax_history": [
    {"year": "number", "season": "string or null", "total_tax": "number", "total_paid": "number", "total_due": "number"}
  ],
  "sale_history": [
    {"date": "string", "price": "number", "buyer": "string or null"}
  ]
}

Here is the text content from the property records page:

---
{content}
---

Extract the property data as JSON:"""


def strip_html_boilerplate(html: str) -> str:
    """Remove navigation, scripts, styles, and other noise from HTML.
    Reduces token count by 60-80%."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"]):
        tag.decompose()

    for cls in ["cookie", "banner", "popup", "modal", "sidebar", "menu", "nav"]:
        for el in soup.find_all(class_=re.compile(cls, re.I)):
            el.decompose()

    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


async def extract_with_llm(
    raw_html: str,
    api_key: str | None = None,
    model: str = "gemini-2.0-flash",
) -> dict | None:
    """Send cleaned page text to Google Gemini for structured extraction."""
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("No GEMINI_API_KEY configured, skipping LLM extraction")
        return None

    clean_text = strip_html_boilerplate(raw_html)

    if len(clean_text) > 25000:
        clean_text = clean_text[:25000] + "\n... [truncated]"

    prompt = EXTRACTION_PROMPT.format(content=clean_text)

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "temperature": 0.0,
                "response_mime_type": "application/json",
            },
        )

        content = response.text
    except ImportError:
        logger.warning("google-genai not installed, falling back to REST API")
        content = await _gemini_rest_api(api_key, model, prompt)

    if not content:
        return None

    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.error("Gemini returned invalid JSON: %s", content[:200])
        return None


async def _gemini_rest_api(api_key: str, model: str, prompt: str) -> str | None:
    """Fallback: call Gemini via REST if the SDK isn't installed."""
    import httpx

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={api_key}"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.error("Unexpected Gemini response structure: %s", json.dumps(data)[:300])
        return None


def merge_llm_into_record(record: PropertyRecord, llm_data: dict) -> PropertyRecord:
    """Fill missing fields in a PropertyRecord using LLM-extracted data."""
    if not llm_data:
        return record

    simple_fields = [
        "parcel_number", "owner_name", "owner_address", "property_address",
        "property_type", "school_district", "zoning", "legal_description",
    ]
    for field in simple_fields:
        if not getattr(record, field) and llm_data.get(field):
            setattr(record, field, llm_data[field])

    numeric_fields = ["assessed_value", "taxable_value", "sev", "acreage"]
    for field in numeric_fields:
        if not getattr(record, field) and llm_data.get(field):
            try:
                setattr(record, field, float(llm_data[field]))
            except (ValueError, TypeError):
                pass

    if llm_data.get("building_info") and (
        not record.building_info or not record.building_info.year_built
    ):
        try:
            record.building_info = BuildingInfo(**llm_data["building_info"])
        except Exception:
            pass

    if not record.tax_history and llm_data.get("tax_history"):
        for item in llm_data["tax_history"]:
            try:
                record.tax_history.append(TaxRecord(**item))
            except Exception:
                pass

    if not record.sale_history and llm_data.get("sale_history"):
        for item in llm_data["sale_history"]:
            try:
                record.sale_history.append(SaleRecord(**item))
            except Exception:
                pass

    return record
