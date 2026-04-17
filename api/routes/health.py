from fastapi import APIRouter

from api.schemas.responses import HealthResponse
from core.orchestration.pipeline import SCRAPER_MAP

router = APIRouter()

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="ok",
        version=VERSION,
        scrapers_loaded=len(SCRAPER_MAP),
    )
