import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Backend root (folder containing `api`, `core`, `scrapers`, `registry`).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent

sys.path.insert(0, str(_BACKEND_ROOT))
load_dotenv(_REPO_ROOT / ".env")

from api.routes import health, scrape

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Property Scraper API",
    description=(
        "A distributed platform for extracting property tax records, "
        "ownership details, and parcel information from public websites."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scrape.router, prefix="/api/v1", tags=["Scraping"])
app.include_router(health.router, prefix="/api/v1", tags=["Health"])


@app.get("/")
async def root():
    return {
        "service": "Property Scraper API",
        "version": "0.1.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/api/v1/health",
        "scrape_post": "/api/v1/scrape",
    }
