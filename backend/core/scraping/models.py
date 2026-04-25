from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SaleRecord(BaseModel):
    date: Optional[str] = None
    price: Optional[float] = None
    buyer: Optional[str] = None
    seller: Optional[str] = None
    instrument: Optional[str] = None


class TaxRecord(BaseModel):
    year: Optional[int] = None
    season: Optional[str] = None
    total_tax: Optional[float] = None
    total_paid: Optional[float] = None
    total_due: Optional[float] = None
    last_paid: Optional[str] = None
    # Cook County IL (and similar): certified/board/mailed assessed total for that roll year
    assessed_total: Optional[float] = None


class LoanRecord(BaseModel):
    recorded_date: Optional[str] = None
    execution_date: Optional[str] = None
    amount: Optional[float] = None
    document_number: Optional[str] = None
    document_type: Optional[str] = None
    property_address: Optional[str] = None
    source_name: Optional[str] = None


class BuildingInfo(BaseModel):
    year_built: Optional[int] = None
    style: Optional[str] = None
    exterior: Optional[str] = None
    total_living_area: Optional[str] = None
    heating_type: Optional[str] = None
    bedrooms: Optional[str] = None
    bathrooms: Optional[str] = None
    fireplace: Optional[str] = None
    stories: Optional[str] = None
    basement: Optional[str] = None
    garage: Optional[str] = None


class PropertyRecord(BaseModel):
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
    # Cook Treasurer Playwright enrich: merged | treasurer_shell_no_bills | … (see cook_treasurer_tax)
    cook_treasurer_tax_status: Optional[str] = None
    raw_html: Optional[str] = None
    screenshot_path: Optional[str] = None
