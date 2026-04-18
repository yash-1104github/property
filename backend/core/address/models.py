from pydantic import BaseModel


class NormalizedAddress(BaseModel):
    raw_input: str
    street_number: str | None = None
    street_name: str | None = None
    street_suffix: str | None = None
    street_direction: str | None = None
    unit: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    county: str | None = None
    country: str = "US"
    latitude: float | None = None
    longitude: float | None = None
    pipeline_id: str | None = None

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
        parts = list(filter(None, [street, self.city, self.state, self.zip_code]))
        if self.country and self.country.upper() != "US":
            parts.append(self.country)
        return ", ".join(parts)
