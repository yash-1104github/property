# Distributed Property Data Scraping Platform — Architecture & Strategy

## 1. Problem Statement

Build a scalable, distributed web scraping platform that accepts a **property address** as input and extracts structured property data (tax records, ownership details, parcel info, legal records) from public websites worldwide — handling diverse countries, languages, website structures, unknown endpoints, and dynamic/protected sites.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT / API GATEWAY                         │
│  REST API  ·  GraphQL  ·  Webhook Callbacks  ·  Dashboard UI        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       ORCHESTRATION LAYER                           │
│  Job Scheduler  ·  Address Router  ·  Priority Queue  ·  Retry Mgr │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
┌──────────────────┐ ┌─────────────────┐ ┌──────────────────────────┐
│  DISCOVERY ENGINE│ │  SCRAPER FLEET  │ │  AI EXTRACTION PIPELINE  │
│  (find the right │ │  (headless      │ │  (LLM-powered parsing,   │
│   website/page)  │ │   browsers,     │ │   translation, schema    │
│                  │ │   rotating      │ │   mapping)               │
│                  │ │   proxies)      │ │                          │
└────────┬─────────┘ └────────┬────────┘ └────────────┬─────────────┘
         │                    │                        │
         ▼                    ▼                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA LAYER                                  │
│  Structured Store (Postgres)  ·  Raw HTML Cache (S3)                │
│  Vector Store (Embeddings)    ·  Knowledge Graph (Neo4j)            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Modules

### 3.1 Address Ingestion & Normalization

The entry point. A raw address string goes through normalization before anything else.

| Step | What Happens | Tools / Approach |
|---|---|---|
| Parse | Break address into components (street, city, state, zip, country) | `libpostal` (open-source address parser trained on OpenStreetMap data for 200+ countries) |
| Geocode | Resolve to lat/lng and confirm country/region | Google Geocoding API, Nominatim (OSM), or Pelias |
| Normalize | Standardize abbreviations, transliterate non-Latin scripts | ICU transliteration, country-specific formatting rules |
| Classify | Determine which country → state → municipality pipeline to invoke | Lookup table + fallback heuristics |

**Output:** A structured `NormalizedAddress` object with a `pipeline_id` pointing to the right scraping strategy.

---

### 3.2 Discovery Engine — Finding the Right Website

This is the hardest unsolved problem: *given an address in an arbitrary location, find the correct government/public website that holds property records.*

#### Strategy: Multi-Layer Discovery

**Layer 1 — Curated Registry (high confidence)**
Maintain a manually curated + community-contributed database mapping:

```
country → state/province → county/municipality → website URL + entry point
```

Start with the US (3,000+ county assessor sites), then expand to Canada, UK, Australia, India, etc. Store as structured JSON/YAML:

```yaml
# registry/us/california/los-angeles.yaml
jurisdiction:
  country: US
  state: CA
  county: Los Angeles
sources:
  - name: LA County Assessor
    url: https://assessor.lacounty.gov/
    search_page: https://portal.assessor.lacounty.gov/parceldetail
    search_type: address  # or parcel_number, owner_name
    data_types: [tax, ownership, parcel, valuation]
    scraper: us_la_county_assessor  # custom scraper module
    anti_bot: cloudflare
    last_verified: 2026-03-15
```

**Layer 2 — AI-Powered Search Discovery (medium confidence)**
For jurisdictions without a curated entry:

1. Use a search engine API (Google Custom Search, SerpAPI, Brave Search) with queries like:
   - `"{county name}" property tax records search`
   - `"{municipality}" land registry online`
   - `"{address}" parcel assessment`
2. Feed candidate URLs to an LLM to classify: *"Is this a government property records portal?"*
3. If yes, attempt automated navigation (see §3.3).

**Layer 3 — Recursive Crawl Discovery (low confidence, high coverage)**
For countries with no known registry entry and failed search:

1. Start from known government domain patterns (e.g., `.gov`, `.gc.ca`, `.gov.uk`, `.nic.in`)
2. Crawl sitemap or homepage looking for keywords: *"property", "tax", "assessment", "land records", "cadaster"*
3. Build a navigation graph and use an LLM to identify the most promising path to a search form.

---

### 3.3 Scraper Fleet

#### Two-Tier Architecture

**Tier 1 — Lightweight HTTP Scrapers (fast, cheap)**
For static or lightly-protected sites:

- `httpx` / `aiohttp` with async concurrency
- Parsel / lxml for HTML parsing
- Handle: simple HTML forms, server-rendered pages, basic pagination

**Tier 2 — Headless Browser Scrapers (powerful, expensive)**
For JavaScript-heavy, CAPTCHA-protected, or dynamically-loaded sites:

- **Playwright** (preferred over Selenium — faster, more reliable)
- Run in containerized pools (Kubernetes pods or AWS Fargate)
- Features:
  - Wait for network idle / specific selectors
  - Intercept and replay XHR/fetch requests
  - Handle cookie consent banners automatically
  - Screenshot for debugging / audit trail

#### Anti-Bot & Protection Handling

| Challenge | Solution |
|---|---|
| Rate limiting | Adaptive throttling per domain, exponential backoff |
| IP blocking | Rotating residential proxy pool (Bright Data, Oxylabs, or self-hosted via cloud IPs) |
| CAPTCHAs | CAPTCHA-solving services (2Captcha, Anti-Captcha) as last resort; prefer session reuse to avoid triggers |
| Cloudflare / Akamai | `undetected-chromedriver`, TLS fingerprint rotation, `curl_cffi` |
| Login walls | Credential vault per jurisdiction (for public record portals requiring free registration) |
| Dynamic tokens | Intercept API calls from browser, extract tokens, replay with HTTP client |

#### Scraper Types

```
scrapers/
├── generic/
│   ├── form_submitter.py       # Detects search forms, fills address fields, submits
│   ├── table_extractor.py      # Extracts data from HTML tables
│   └── pdf_extractor.py        # Downloads and parses PDF tax bills
├── country/
│   ├── us/
│   │   ├── base_us_assessor.py # Common patterns across US county sites
│   │   ├── la_county.py        # LA County specific
│   │   ├── cook_county.py      # Cook County specific
│   │   └── ...
│   ├── uk/
│   │   ├── land_registry.py
│   │   └── council_tax.py
│   ├── india/
│   │   ├── igr_maharashtra.py
│   │   └── ...
│   └── ...
└── ai_adaptive/
    └── llm_guided_scraper.py   # Uses LLM to navigate unknown sites
```

---

### 3.4 AI Extraction Pipeline — The Intelligence Layer

This is where the platform differentiates from traditional scrapers.

#### Step 1: Raw Content Capture

After a page is loaded (via HTTP or headless browser), capture:
- Clean HTML (after JS execution)
- Screenshot (PNG)
- All network requests/responses (HAR file)
- PDF documents linked from the page

#### Step 2: LLM-Powered Data Extraction

Feed the HTML/screenshot to Google Gemini with a structured extraction prompt:

```
Given this HTML from a property records website, extract the following
fields into JSON. If a field is not present, use null.

Target schema:
{
  "owner_name": "string",
  "owner_mailing_address": "string",
  "parcel_number": "string",
  "legal_description": "string",
  "property_type": "residential | commercial | land | industrial",
  "year_built": "integer",
  "lot_size_sqft": "number",
  "building_size_sqft": "number",
  "assessed_value": "number",
  "market_value": "number",
  "tax_amount_annual": "number",
  "tax_year": "integer",
  "tax_status": "paid | delinquent | exempt",
  "sale_history": [{"date": "ISO date", "price": "number", "buyer": "string"}],
  "zoning": "string",
  "school_district": "string"
}

HTML content:
{html_content}
```

**Why LLM over traditional parsing?**
- No need to write CSS selectors per site — the LLM understands semantic structure
- Handles slight layout changes without breaking
- Works across languages (the LLM translates on the fly)
- Can interpret ambiguous labels (e.g., "AV" → "Assessed Value")

**Cost control:**
- Use Gemini 2.0 Flash for initial extraction (fast, cheap, 1M token context)
- Escalate to Gemini 2.5 Pro only for ambiguous/complex pages
- Cache extraction results aggressively — same site layout = reuse prompt template
- Pre-filter HTML: strip nav, footer, ads, scripts before sending to LLM (reduce tokens by 60-80%)

#### Step 3: Translation (for non-English sites)

- Detect language via `langdetect` or LLM
- If non-English, translate field labels and values as part of the extraction prompt
- Store both original-language and English-translated versions

#### Step 4: Confidence Scoring & Validation

Every extracted field gets a confidence score:

| Signal | Weight |
|---|---|
| LLM self-reported confidence | 0.3 |
| Cross-reference with geocoded address | 0.2 |
| Schema validation (data types, ranges) | 0.2 |
| Historical consistency (same parcel, prior scrape) | 0.2 |
| Source reliability (curated vs discovered) | 0.1 |

Records below a configurable threshold are flagged for human review.

---

### 3.5 Orchestration Layer

Built on **Celery + Redis** (or **Temporal** for complex workflows):

```
                     ┌──────────────┐
                     │  API Request  │
                     │  (address)    │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │   Normalize   │
                     │   Address     │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │   Route to    │
                     │   Pipeline    │
                     └──────┬───────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌───────────┐ ┌──────────┐ ┌──────────┐
        │ Known Site│ │ Search   │ │ Deep     │
        │ Scraper   │ │ Discover │ │ Discovery│
        └─────┬─────┘ └────┬─────┘ └────┬─────┘
              │             │             │
              └─────────────┼─────────────┘
                            │
                     ┌──────▼───────┐
                     │  Extract &   │
                     │  Validate    │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │  Store &     │
                     │  Callback    │
                     └──────────────┘
```

Key orchestration features:
- **Retry with escalation**: HTTP fails → try headless browser → try different proxy → flag for manual review
- **Deduplication**: Same address requested twice within TTL → return cached result
- **Rate governor**: Per-domain concurrency limits to stay respectful and avoid bans
- **Priority queues**: Paid/urgent requests jump ahead; bulk jobs run during off-peak

---

### 3.6 Data Layer

#### Primary Store — PostgreSQL

```sql
CREATE TABLE properties (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    normalized_addr JSONB NOT NULL,
    geocode         GEOGRAPHY(Point, 4326),
    country_code    CHAR(2) NOT NULL,
    jurisdiction    TEXT,

    -- Extracted data
    owner_name      TEXT,
    parcel_number   TEXT,
    property_type   TEXT,
    assessed_value  NUMERIC,
    market_value    NUMERIC,
    tax_amount      NUMERIC,
    tax_year        INT,
    raw_data        JSONB,         -- Full extraction output
    confidence      REAL,

    -- Metadata
    source_url      TEXT,
    scrape_job_id   UUID,
    scraped_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),

    -- Indexing
    CONSTRAINT uq_parcel UNIQUE (country_code, jurisdiction, parcel_number)
);

CREATE INDEX idx_properties_geocode ON properties USING GIST (geocode);
CREATE INDEX idx_properties_addr ON properties USING GIN (normalized_addr);
```

#### Raw Cache — S3 / MinIO

Store raw HTML, screenshots, PDFs, HAR files for:
- Debugging extraction failures
- Re-running extraction with improved prompts without re-scraping
- Legal compliance / audit trail

#### Knowledge Graph — Neo4j (optional, phase 2)

Model relationships:
- `(Property)-[:OWNED_BY]->(Person)`
- `(Property)-[:LOCATED_IN]->(Jurisdiction)`
- `(Person)-[:ALSO_OWNS]->(Property)`
- `(Property)-[:TRANSFERRED_TO {date, price}]->(Person)`

Enables powerful queries: *"Find all properties owned by X across all jurisdictions"*

---

## 4. Handling the Hard Parts

### 4.1 Unknown Endpoints / Never-Seen-Before Sites

This is the core innovation. The **AI Adaptive Scraper** works like a human researcher:

```
1. Land on a candidate government website
2. Take a screenshot → send to vision LLM
3. LLM identifies: "This looks like a property search portal.
   I see a search box labeled 'Address'. I should type the address and click 'Search'."
4. Execute the action via Playwright
5. Observe the result page → extract data
6. If multi-step (e.g., "click on parcel number to see details"), continue navigating
7. Record the successful navigation path as a reusable "recipe" for this site
```

**Recipe caching**: Once a navigation path works for a site, serialize it as a deterministic script. Future requests for the same jurisdiction skip the LLM entirely and replay the recipe. LLM only re-engages if the recipe fails (site redesign).

### 4.2 Different Countries & Languages

| Challenge | Approach |
|---|---|
| Address formats vary wildly | `libpostal` handles 200+ countries out of the box |
| Non-Latin scripts (Chinese, Arabic, Devanagari) | LLM extraction handles natively; `libpostal` supports transliteration |
| Right-to-left layouts | Playwright renders correctly; LLM vision model handles RTL |
| Different property systems (Torrens, deed, etc.) | Country-specific schema extensions; base schema covers common fields |
| Legal/regulatory differences | Per-country compliance rules in the registry config |

### 4.3 Dynamic & Protected Websites

Escalation ladder:

```
Level 0: Plain HTTP request
   ↓ (fails: JS-rendered content)
Level 1: Headless Playwright
   ↓ (fails: bot detection)
Level 2: Stealth Playwright + residential proxy
   ↓ (fails: CAPTCHA)
Level 3: CAPTCHA solving service
   ↓ (fails: requires login)
Level 4: Automated registration + session management
   ↓ (fails: completely blocked)
Level 5: Flag for manual intervention / alternative data source
```

---

## 5. Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **Language** | Python 3.12+ | Ecosystem for scraping (Scrapy, Playwright, BeautifulSoup), ML/AI libs |
| **API** | FastAPI | Async, fast, auto-docs |
| **Task Queue** | Temporal (or Celery + Redis) | Durable workflows, retry logic, visibility |
| **Browser Automation** | Playwright | Faster than Selenium, better API, built-in waiting |
| **HTTP Client** | httpx + curl_cffi | Async HTTP + TLS fingerprint spoofing |
| **LLM** | Google Gemini 2.0 Flash / 2.5 Pro | Structured extraction, site navigation, 1M token context |
| **Database** | PostgreSQL + PostGIS | Geospatial queries, JSONB for flexible schema |
| **Object Storage** | S3 / MinIO | Raw HTML, screenshots, PDFs |
| **Cache** | Redis | Job dedup, rate limiting, session store |
| **Proxy** | Bright Data / self-hosted | Residential IP rotation |
| **Container** | Docker + Kubernetes | Scale browser pods independently |
| **Monitoring** | Prometheus + Grafana | Scrape success rates, latency, cost tracking |
| **CI/CD** | GitHub Actions | Automated testing of scrapers against fixtures |

---

## 6. Phased Rollout

### Phase 1 — Foundation (Weeks 1-6)
- [ ] Address normalization service (libpostal + geocoding)
- [ ] Curated registry for top 50 US counties
- [ ] 10 hand-written scrapers for high-value jurisdictions
- [ ] Basic API: submit address → get structured result
- [ ] PostgreSQL data store + S3 raw cache
- [ ] Single-node deployment

### Phase 2 — Intelligence (Weeks 7-12)
- [ ] LLM extraction pipeline (replace per-site CSS selectors)
- [ ] AI search discovery (find websites for uncovered jurisdictions)
- [ ] AI adaptive scraper (navigate unknown sites)
- [ ] Recipe caching system
- [ ] Confidence scoring + human review queue
- [ ] Expand to 500 US counties

### Phase 3 — Scale (Weeks 13-18)
- [ ] Kubernetes deployment with auto-scaling browser pods
- [ ] Rotating proxy infrastructure
- [ ] Anti-bot escalation ladder
- [ ] Temporal workflows for complex multi-step scrapes
- [ ] Monitoring dashboard (success rates, coverage map, cost per scrape)
- [ ] Expand internationally (UK, Canada, Australia)

### Phase 4 — Platform (Weeks 19-24)
- [ ] Community-contributed scraper registry
- [ ] Self-service dashboard for users
- [ ] Bulk scraping with CSV upload
- [ ] Webhook / streaming results
- [ ] Knowledge graph for ownership analysis
- [ ] Expand to 20+ countries

---

## 7. Project Structure

```
property-scraper/
├── api/
│   ├── main.py                  # FastAPI application
│   ├── routes/
│   │   ├── scrape.py            # POST /scrape — submit address
│   │   ├── results.py           # GET /results/{job_id}
│   │   └── health.py
│   └── schemas/
│       ├── address.py           # Pydantic models for address input
│       └── property.py          # Pydantic models for property output
│
├── core/
│   ├── address/
│   │   ├── normalizer.py        # libpostal + geocoding integration
│   │   ├── router.py            # Maps address → pipeline
│   │   └── models.py
│   ├── discovery/
│   │   ├── registry.py          # Curated jurisdiction registry
│   │   ├── search_discovery.py  # AI-powered website finder
│   │   └── crawl_discovery.py   # Deep crawl fallback
│   ├── scraping/
│   │   ├── http_scraper.py      # Tier 1 lightweight scraper
│   │   ├── browser_scraper.py   # Tier 2 Playwright scraper
│   │   ├── stealth.py           # Anti-bot measures
│   │   └── proxy_manager.py     # Proxy rotation
│   ├── extraction/
│   │   ├── llm_extractor.py     # LLM-powered field extraction
│   │   ├── pdf_extractor.py     # PDF parsing
│   │   ├── translator.py        # Language detection + translation
│   │   └── validator.py         # Confidence scoring + validation
│   └── orchestration/
│       ├── workflows.py         # Temporal/Celery workflow definitions
│       ├── scheduler.py         # Job scheduling + priority
│       └── retry.py             # Retry + escalation logic
│
├── scrapers/                    # Jurisdiction-specific scrapers
│   ├── generic/
│   │   ├── form_submitter.py
│   │   └── table_extractor.py
│   ├── us/
│   │   ├── __init__.py
│   │   ├── la_county.py
│   │   ├── cook_county.py
│   │   └── ...
│   ├── uk/
│   ├── india/
│   └── ...
│
├── registry/                    # Jurisdiction registry (YAML configs)
│   ├── us/
│   │   ├── california/
│   │   │   ├── los-angeles.yaml
│   │   │   └── san-francisco.yaml
│   │   └── illinois/
│   │       └── cook.yaml
│   ├── uk/
│   └── ...
│
├── recipes/                     # Cached navigation recipes (auto-generated)
│   └── {domain_hash}.json
│
├── infrastructure/
│   ├── docker/
│   │   ├── Dockerfile.api
│   │   ├── Dockerfile.worker
│   │   └── Dockerfile.browser
│   ├── k8s/
│   │   ├── api-deployment.yaml
│   │   ├── worker-deployment.yaml
│   │   └── browser-pool.yaml
│   └── terraform/
│       └── main.tf
│
├── tests/
│   ├── fixtures/                # Saved HTML pages for regression testing
│   ├── test_normalizer.py
│   ├── test_extraction.py
│   └── test_scrapers/
│
├── docker-compose.yaml
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

## 8. Key API Endpoints

```
POST /api/v1/scrape
  Body: { "address": "123 Main St, Los Angeles, CA 90012" }
  Response: { "job_id": "uuid", "status": "queued", "estimated_time_seconds": 30 }

GET /api/v1/results/{job_id}
  Response: {
    "status": "completed",
    "confidence": 0.92,
    "data": {
      "owner_name": "John Doe",
      "parcel_number": "5432-001-019",
      "assessed_value": 850000,
      "tax_amount": 10234.56,
      ...
    },
    "source": {
      "url": "https://assessor.lacounty.gov/...",
      "scraped_at": "2026-04-14T10:30:00Z"
    }
  }

GET /api/v1/coverage
  Response: { "countries": 12, "jurisdictions": 2847, "success_rate": 0.87 }

POST /api/v1/scrape/bulk
  Body: { "addresses": ["...", "..."], "webhook_url": "https://..." }
```

---

## 9. Cost Estimation (per scrape)

| Component | Cost per Request | Notes |
|---|---|---|
| Address normalization | ~$0.001 | Self-hosted libpostal |
| Geocoding | ~$0.005 | Google at scale pricing |
| HTTP scrape | ~$0.001 | Minimal compute |
| Browser scrape | ~$0.01-0.05 | Compute + memory for headless browser |
| Proxy | ~$0.01-0.10 | Residential proxy per request |
| LLM extraction (flash) | ~$0.001-0.005 | Gemini 2.0 Flash (1M context, very cheap) |
| LLM extraction (pro) | ~$0.01-0.05 | Gemini 2.5 Pro (only for complex pages) |
| Storage | ~$0.001 | S3 + Postgres |
| **Total (typical)** | **~$0.03-0.15** | Depends on site complexity |

At 100K scrapes/month: **$3,000 - $15,000/month** estimated infrastructure cost.

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Sites change layouts frequently | Broken scrapers | LLM extraction is layout-agnostic; recipe auto-regeneration |
| IP bans from aggressive scraping | Data unavailable | Respectful rate limiting, rotating proxies, session reuse |
| Legal restrictions in some jurisdictions | Compliance issues | Per-country legal review; only scrape public records; respect robots.txt |
| LLM hallucination in extraction | Bad data | Confidence scoring, cross-validation, human review for low-confidence |
| Cost explosion from LLM calls | Budget overrun | Gemini Flash is very cheap; tiered model strategy, aggressive caching, HTML pre-filtering |
| CAPTCHAs becoming harder | Reduced access | CAPTCHA service fallback, prefer API access where available |
| Single point of failure | Downtime | Distributed architecture, no single critical path, circuit breakers |

---

## 11. Success Metrics

| Metric | Target (Phase 1) | Target (Phase 4) |
|---|---|---|
| Jurisdictions covered | 50 US counties | 5,000+ across 20 countries |
| Extraction accuracy | 85%+ | 95%+ |
| Average response time | < 60 seconds | < 30 seconds |
| Scrape success rate | 80% | 92%+ |
| Cost per scrape | < $0.20 | < $0.08 |
| Uptime | 99% | 99.9% |

---

## 12. Getting Started

```bash
# Clone and setup
git clone <repo-url> && cd property-scraper

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env
# Edit .env with your API keys (Gemini, geocoding, proxy provider)

# Start infrastructure
docker-compose up -d  # Postgres, Redis, MinIO

# Run API
uvicorn api.main:app --reload

# Run worker
celery -A core.orchestration.workflows worker --loglevel=info

# Test a scrape
curl -X POST http://localhost:8000/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St, Los Angeles, CA 90012"}'
```
