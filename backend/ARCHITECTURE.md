# Backend Architecture

This document describes the architecture of the property backend system.

## Overview

The backend is a Python-based API service that handles property data scraping, extraction, and discovery across various US counties. It follows a modular architecture with clear separation of concerns.

## Directory Structure

```
backend/
├── api/                    # FastAPI application and routes
├── core/                   # Core business logic
├── scrapers/               # County-specific scrapers
├── registry/               # Configuration files for supported regions
├── infrastructure/         # Database scripts and infrastructure
├── requirements.txt       # Python dependencies
├── __main__.py            # Entry point for running the server
└── run_demo.py           # Demo script for testing
```

---

## API Layer (`api/`)

FastAPI application exposing HTTP endpoints for property data operations.

### Files

| File | Description |
|------|-------------|
| `__init__.py` | API package initialization |
| `main.py` | FastAPI app creation, middleware configuration, and startup events |
| `routes/health.py` | Health check endpoint (`GET /health`) |
| `routes/scrape.py` | Scrape endpoints for property data retrieval |
| `schemas/requests.py` | Pydantic models for request validation |
| `schemas/responses.py` | Pydantic models for response serialization |

### Key Components

- **FastAPI App**: Main application instance in `main.py`
- **Routes**: 
  - `health.py` — Health monitoring
  - `scrape.py` — Property scraping endpoints
- **Schemas**: Request/response validation using Pydantic

---

## Core Layer (`core/`)

Business logic and domain models for the property system.

### Modules

#### `core/address/`
| File | Description |
|------|-------------|
| `models.py` | Address data models and schemas |
| `normalizer.py` | Address normalization and validation logic |

#### `core/discovery/`
| File | Description |
|------|-------------|
| `netronline.py` | Netronline property discovery |
| `registry.py` | Site registry management |
| `site_repository.py` | Repository pattern for site data |
| `source_resolver.py` | Resolves data sources for properties |

#### `core/extraction/`
| File | Description |
|------|-------------|
| `llm_extractor.py` | LLM-based property data extraction |

#### `core/orchestration/`
| File | Description |
|------|-------------|
| `pipeline.py` | Orchestration pipeline for multi-step property processing |

#### `core/scraping/`
| File | Description |
|------|-------------|
| `base.py` | Base scraper class and common scraping utilities |
| `models.py` | Scraping-related data models |

---

## Scrapers Layer (`scrapers/`)

County-specific and generic scraper implementations.

### Generic Scrapers (`scrapers/generic/`)
| File | Description |
|------|-------------|
| `http_scraper.py` | Generic HTTP-based scraper |

### US Scrapers (`scrapers/us/`)

#### Illinois (`scrapers/us/illinois/`)
| File | Description |
|------|-------------|
| `cook_assessor_parcel_addresses.py` | Cook County Assessor parcel addresses |
| `cook_clerk_recording_loans.py` | **NEW** — Cook County Recorder of Deeds loan/mortgage recordings via Socrata API |
| `cook_treasurer_tax.py` | Cook County Treasurer tax data |

#### Michigan (`scrapers/us/michigan/`)
| File | Description |
|------|-------------|
| `arcgis_parcel_query.py` | ArcGIS-based parcel query |
| `bsa_online.py` | BSA Online scraper |

#### Regrid (`scrapers/us/`)
| File | Description |
|------|-------------|
| `regrid_parcel.py` | Regrid parcel data scraper |

---

## Registry Layer (`registry/`)

Configuration files defining supported regions and their properties.

### Files

| File | Description |
|------|-------------|
| `us/arizona/maricopa.yaml` | Maricopa County, AZ configuration |
| `us/illinois/cook.yaml` | Cook County, IL configuration (includes loan_history source) |
| `us/michigan/calhoun.yaml` | Calhoun County, MI configuration |
| `us/texas/harris.yaml` | Harris County, TX configuration |

Each YAML file contains:
- County-specific settings
- Data source endpoints
- Scraping configuration
- Field mappings

---

## Infrastructure Layer (`infrastructure/`)

Database and infrastructure-related files.

### SQL Scripts (`infrastructure/sql/`)
| File | Description |
|------|-------------|
| `seed_calhoun_example.sql` | Seed data for Calhoun County example |
| `site_registry.sql` | Site registry table schema and data |

---

## Entry Points

| File | Description |
|------|-------------|
| `__main__.py` | Main entry point — runs the FastAPI server |
| `run_demo.py` | Demo script for testing functionality |
| `fetch_calhoun_portal_data.py` | Utility script for fetching Calhoun portal data |

---

## Dependencies

See `requirements.txt` for Python package dependencies.

---

## Data Flow

1. **API Request** → `api/routes/scrape.py`
2. **Orchestration** → `core/orchestration/pipeline.py`
3. **Discovery** → `core/discovery/` (registry, source resolver)
4. **Scraping** → `scrapers/` (county-specific implementations)
5. **Extraction** → `core/extraction/llm_extractor.py`
6. **Response** → `api/schemas/responses.py`

---

## Technology Stack

- **Framework**: FastAPI
- **HTTP Client**: httpx / requests
- **Data Validation**: Pydantic
- **LLM Integration**: OpenAI / Anthropic (for extraction)
- **Database**: PostgreSQL (via SQL scripts)
- **Configuration**: YAML files

---

## Loan History Feature (NEW)

Added support for fetching mortgage/loan recordings from external data sources.

### Data Source
- **Cook County Recorder of Deeds** — Open Data (Socrata API)
- **Endpoint**: `https://datacatalog.cookcountyil.gov/resource/fc9e-k9vn.json`
- **Auth**: None required (optionally set `SOCRATA_APP_TOKEN` env var for higher rate limits)

### Implementation Details

| File | Description |
|------|-------------|
| `api/schemas/requests.py` | Added `include_loan_history` field to `ScrapeRequest` |
| `api/routes/scrape.py` | Passes `include_loan_history` to pipeline |
| `core/orchestration/pipeline.py` | Added loan history fetch step (Step 3c) |
| `scrapers/us/illinois/cook_clerk_recording_loans.py` | Full Socrata API implementation with PIN and address lookup |

### Lookup Strategies
1. **Primary**: PIN-based lookup (uses parcel_number from assessor scraper)
2. **Fallback**: Address-based lookup (street_number + street_name + zip)

### Document Types Captured
- MORTGAGE, MTG, DEED OF TRUST, DOT
- HOME EQUITY, HELOC, CONSTRUCTION LOAN
- LAND TRUST MORTGAGE, ASSIGNMENT OF MORTGAGE
- RELEASE OF MORTGAGE, SATISFACTION OF MORTGAGE
- PARTIAL RELEASE, SUBORDINATION AGREEMENT

### Usage
```json
{
  "address": "4406 W Wilson Ave, Chicago, IL 60630",
  "county": "Cook",
  "include_loan_history": true
}
```

### Response Fields
| Field | Description |
|-------|-------------|
| `instrument_number` | Unique recording instrument ID |
| `doc_type` | Document type (e.g., "MORTGAGE") |
| `grantor` | Borrower / mortgagor name |
| `grantee` | Lender / mortgagee name |
| `recording_date` | Date filed (YYYY-MM-DD) |
| `consideration` | Loan amount in USD |
| `pin` | 14-digit Parcel Identification Number |
| `legal_description` | Abbreviated legal description |