from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from core.scraping.models import BuildingInfo, LoanRecord, SaleRecord, TaxRecord


class AddressResponse(BaseModel):
    raw_input: str
    street_number: Optional[str] = None
    street_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    country: str = "US"
    pipeline_id: Optional[str] = None


class PropertyResponse(BaseModel):
    parcel_number: Optional[str] = None
    owner_name: Optional[str] = None
    owner_address: Optional[str] = None
    property_address: Optional[str] = None
    property_type: Optional[str] = None
    school_district: Optional[str] = None
    zoning: Optional[str] = None

    assessed_value: Optional[float] = None
    taxable_value: Optional[float] = None
    sev: Optional[float] = None

    acreage: Optional[float] = None
    lot_size_sqft: Optional[float] = None
    legal_description: Optional[str] = None

    building_info: Optional[BuildingInfo] = None
    tax_history: list[TaxRecord] = []
    sale_history: list[SaleRecord] = []
    loan_history: list[LoanRecord] = []

    source_url: Optional[str] = None
    source_name: Optional[str] = None
    scraped_at: Optional[datetime] = None
    confidence: float = 0.0
    cook_treasurer_tax_status: Optional[str] = None


class ScrapeResponse(BaseModel):
    success: bool
    address: AddressResponse
    data: Optional[PropertyResponse] = None
    error: Optional[str] = None
    duration_ms: int = 0


class HealthResponse(BaseModel):
    status: str
    version: str
    scrapers_loaded: int
