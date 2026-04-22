from datetime import datetime
from pydantic import BaseModel


class SaleRecord(BaseModel):
    date: str | None = None
    price: float | None = None
    buyer: str | None = None
    seller: str | None = None
    instrument: str | None = None


class TaxRecord(BaseModel):
    year: int | None = None
    season: str | None = None
    total_tax: float | None = None
    total_paid: float | None = None
    total_due: float | None = None
    last_paid: str | None = None
    # Cook County IL (and similar): certified/board/mailed assessed total for that roll year
    assessed_total: float | None = None


class LoanRecord(BaseModel):
    recorded_date: str | None = None
    execution_date: str | None = None
    amount: float | None = None
    document_number: str | None = None
    document_type: str | None = None
    property_address: str | None = None
    source_name: str | None = None


class BuildingInfo(BaseModel):
    year_built: int | None = None
    style: str | None = None
    exterior: str | None = None
    total_living_area: str | None = None
    heating_type: str | None = None
    bedrooms: str | None = None
    bathrooms: str | None = None
    fireplace: str | None = None
    stories: str | None = None
    basement: str | None = None
    garage: str | None = None


class PropertyRecord(BaseModel):
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
    loan_history: list[LoanRecord] = []

    source_url: str | None = None
    source_name: str | None = None
    scraped_at: datetime | None = None
    confidence: float = 0.0
    # Cook Treasurer Playwright enrich: merged | treasurer_shell_no_bills | … (see cook_treasurer_tax)
    cook_treasurer_tax_status: str | None = None
    raw_html: str | None = None
    screenshot_path: str | None = None
