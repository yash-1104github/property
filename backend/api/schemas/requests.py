import os

from pydantic import BaseModel, ConfigDict, Field


def _default_use_llm() -> bool:
    """Respect ``USE_LLM`` in the environment when the client omits ``use_llm`` (default: on)."""
    v = (os.getenv("USE_LLM") or "true").strip().lower()
    return v in ("1", "true", "yes", "on")


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    address: str = Field(
        ...,
        min_length=5,
        examples=["21013 DANA Drive, Battle Creek, MI 49017"],
        description="Full US street address to look up",
    )
    county: str | None = Field(
        None,
        examples=["Calhoun"],
        description="US county name (helps route to the correct assessor / tax portal)",
    )
    use_llm: bool = Field(
        default_factory=_default_use_llm,
        description=(
            "Use Gemini to enrich extraction when ``GEMINI_API_KEY`` is set. "
            "Omit this field to use the ``USE_LLM`` env default (default: true)."
        ),
    )
