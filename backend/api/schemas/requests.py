from pydantic import BaseModel, Field, field_validator


class ScrapeRequest(BaseModel):
    address: str = Field(
        ...,
        min_length=5,
        examples=["21013 DANA Drive, Battle Creek, MI 49017"],
        description="Full property address to look up",
    )
    county: str | None = Field(
        None,
        examples=["Calhoun"],
        description="County / district name (helps routing to the right data source)",
    )
    country_code: str | None = Field(
        None,
        min_length=2,
        max_length=2,
        examples=["US", "GB", "DE", "IN"],
        description=(
            "ISO 3166-1 alpha-2 country code. Omit or use US for United States (usaddress). "
            "For any other country, set this and the address is structured via Nominatim (OSM)."
        ),
    )
    use_llm: bool = Field(
        True,
        description="Use LLM to enrich extraction if regex scraping returns incomplete data",
    )

    @field_validator("country_code", mode="before")
    @classmethod
    def _empty_country_to_none(cls, v: object) -> object:
        if v == "" or v is None:
            return None
        return v
