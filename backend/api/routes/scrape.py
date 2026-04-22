import logging

from fastapi import APIRouter

from api.schemas.requests import ScrapeRequest
from api.schemas.responses import AddressResponse, PropertyResponse, ScrapeResponse
from core.orchestration.pipeline import run_pipeline

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/scrape", response_model=ScrapeResponse)
async def scrape_property(req: ScrapeRequest):
    """
    Submit a US property address and receive structured property data
    including tax records, ownership info, and building details where available.
    """
    logger.info("Scrape request: %s (county=%s)", req.address, req.county)

    result = await run_pipeline(
        raw_address=req.address,
        county=req.county,
        use_llm=req.use_llm,
    )

    addr_resp = AddressResponse(
        raw_input=result.address.raw_input,
        street_number=result.address.street_number,
        street_name=result.address.street_name,
        city=result.address.city,
        state=result.address.state,
        zip_code=result.address.zip_code,
        county=result.address.county,
        country=result.address.country,
        pipeline_id=result.address.pipeline_id,
    )

    prop_resp = None
    if result.record:
        prop_resp = PropertyResponse(**result.record.model_dump(exclude={"raw_html", "screenshot_path"}))

    return ScrapeResponse(
        success=result.success,
        address=addr_resp,
        data=prop_resp,
        error=result.error,
        duration_ms=result.duration_ms,
    )
