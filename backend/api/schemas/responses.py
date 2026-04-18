from datetime import datetime

from pydantic import BaseModel

from core.scraping.models import BuildingInfo, SaleRecord, TaxRecord


class AddressResponse(BaseModel):
    raw_input: str
    street_number: str | None = None
    street_name: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    county: str | None = None
    country: str = "US"
    latitude: float | None = None
    longitude: float | None = None
    pipeline_id: str | None = None


class PropertyResponse(BaseModel):
    parcel_number: str | None = None
    owner_name: str | None = None
    owner_address: str | None = None
    property_address: str | None = None
    property_type: str | None = None
    school_district: str | None = None
    zoning: str | None = None

    assessed_value: float | None = None
    taxable_value: float | None = None
    sev: float | None = None

    acreage: float | None = None
    lot_size_sqft: float | None = None
    legal_description: str | None = None

    building_info: BuildingInfo | None = None
    tax_history: list[TaxRecord] = []
    sale_history: list[SaleRecord] = []

    source_url: str | None = None
    source_name: str | None = None
    scraped_at: datetime | None = None
    confidence: float = 0.0


class ScrapeResponse(BaseModel):
    success: bool
    address: AddressResponse
    data: PropertyResponse | None = None
    error: str | None = None
    duration_ms: int = 0


class HealthResponse(BaseModel):
    status: str
    version: str
    scrapers_loaded: int
