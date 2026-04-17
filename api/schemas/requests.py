from pydantic import BaseModel, Field


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
        description="County name (helps with routing to the right data source)",
    )
    use_llm: bool = Field(
        True,
        description="Use LLM to enrich extraction if regex scraping returns incomplete data",
    )
