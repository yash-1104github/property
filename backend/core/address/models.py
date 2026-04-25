from typing import Optional

from pydantic import BaseModel


class NormalizedAddress(BaseModel):
    raw_input: str
    street_number: Optional[str] = None
    street_name: Optional[str] = None
    street_suffix: Optional[str] = None
    street_direction: Optional[str] = None
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    country: str = "US"
    pipeline_id: Optional[str] = None

    @property
    def full_street(self) -> str:
        parts = filter(None, [
            self.street_number,
            self.street_direction,
            self.street_name,
            self.street_suffix,
        ])
        return " ".join(parts)

    @property
    def one_line(self) -> str:
        street = self.full_street
        parts = filter(None, [street, self.city, self.state, self.zip_code])
        return ", ".join(parts)
