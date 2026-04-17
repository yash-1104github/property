# Property Data Platform — Reference Summary

This document condenses the **practical implementation guide**, **stakeholder messaging**, and **technical complexity** into one reference. It aligns with this repo’s direction: **PostgreSQL site registry (3000+ portals)**, **ordered fallback**, **HTTP + Playwright**, and **Gemini extraction**.

---

## 1. One-line pitch

**We built a system that behaves like a smart researcher:** you give a property address; it **resolves the correct public portals**, **navigates them like a user when needed**, and returns **clean structured data** (tax, ownership, parcel) with **confidence** and an **audit trail of which source worked**.

---

## 2. The core problem (why this is valuable)

- There is **no single global API** for property/tax records.
- Data lives in **thousands of fragmented government portals** (different vendors, layouts, languages, protections).
- Manual lookup **does not scale**; one-off scrapers **do not scale** (every site change breaks code).

**The product is the platform + registry**, not a single scraper.

---

## 3. Theoretical concept (how we scale)

### 3.1 Registry-driven architecture

- Store each official portal as a **row** in a database (“source registry”).
- A shared **engine** reads those rows and executes the right connector strategy.
- **Adding coverage** becomes **adding/updating rows** (plus occasional new vendor connectors), not rewriting the whole system.

### 3.2 Hierarchical fallback (“try until success”)

For a given normalized address, select candidate sources in a controlled order:

1. **Most specific jurisdiction** (city/township) sources first  
2. **County** sources next  
3. **State / alternate** sources as needed  

Within each tier, order by **`priority`** (lower = earlier). **Stop** on first successful parcel record.

> “Recursive” here means **controlled fallback**, not unbounded crawling of the open web.

### 3.3 Two-tier scraping (cost vs reliability)

- **Tier 1 — HTTP** (`httpx`): fast/cheap when HTML is sufficient.
- **Tier 2 — Browser** (`Playwright`): required for JS-heavy portals (e.g., many BS&A deployments).

Use a registry flag like **`requires_browser`** to choose the tier.

### 3.4 AI extraction (layout resilience)

- Portals change layouts frequently; brittle CSS selectors break often.
- **Gemini** is used to map messy HTML into a **stable JSON schema**, with validation + **confidence scoring**.

---

## 4. Recommended tech stack (reference)

| Layer | Tool | Role |
|------|------|------|
| API | **FastAPI** | Async API, validation, docs |
| Browser automation | **Playwright** | JS-heavy portals, multi-step flows |
| HTTP scraping | **httpx** (+ BeautifulSoup/lxml) | Tier-1 static pages |
| AI extraction | **Gemini API** | Structured extraction from HTML |
| Registry DB | **PostgreSQL** | 3000+ sources, priorities, metadata |
| Cache / dedupe | **Redis** | Short TTL cache for repeat addresses |
| Jobs / scale-out | **Celery + Redis** | Background scrape jobs, retries |
| Evidence | **S3 / MinIO** (optional) | HTML/screenshots for debugging |
| Monitoring | **Grafana + metrics DB** | Success rate, latency, cost per lookup |

This repo currently implements **FastAPI + Playwright + httpx + Gemini + Postgres schema prototype**; Celery/Grafana can be added as you harden operations.

---

## 5. Implementation steps (engineering checklist)

### Step 1 — Source registry (Postgres)

Minimum fields (conceptually; align with your DB migration):

- **Jurisdiction**: country, state, county, (optional) city/municipality  
- **Portal**: URL, vendor type (`bsa`, `tyler`, `qpublic`, `custom`)  
- **Search**: `search_method` / supported methods (`address`, `parcel`, `owner`)  
- **Execution**: `requires_browser`, `priority`, `enabled`  
- **Coverage**: `data_available` / `data_types` (e.g., `tax`, `ownership`, `parcel`)  
- **Ops**: `last_verified`, optional `success_rate`, notes  

**Seed strategy:** start with high-leverage vendor connectors (BS&A, Tyler, QPublic patterns), then fill custom county sites.

### Step 2 — Address normalization

- Parse and standardize address components (street/city/state/ZIP/country).
- **County inference** is a common edge case (ZIP can span counties): maintain a **ZIP→county crosswalk** for US accuracy.

### Step 3 — Source resolver (SQL + ordering)

- Query sources for matching jurisdiction with **city-first, county-next** ordering.
- Within matches, sort by **`priority ASC`**.
- Prefer sources that advertise **`tax`** when tax is the goal (this repo sorts tax-capable sources earlier when `data_types` includes `tax`).

### Step 4 — Scrape with fallback

For each resolved source:

1. Choose Tier-1 vs Tier-2 based on `requires_browser`  
2. Run connector  
3. If success → extract + validate + return  
4. Else → log failure → next source  

### Step 5 — Gemini extraction + validation

- Strip boilerplate HTML to reduce tokens.
- Require **JSON output** with a strict schema.
- Validate types/ranges; compute **confidence** from field completeness.

### Step 6 — API + audit logs

- Endpoint: submit address → normalized address + data + `source_used` + confidence.
- Log each attempt: source id, success/failure, latency, optional HTML snapshot path.

---

## 6. Complexity map (what to tell engineering leadership)

| Area | Why it’s hard | Mitigation |
|------|----------------|------------|
| Data fragmentation | Thousands of different portals | Registry + vendor connectors |
| Dynamic sites | React/Angular SPAs | Playwright + waits for async grids |
| Anti-bot | rate limits, blocks, “security verification” | throttling, proxies (where allowed), fallback sources |
| Layout drift | redesigns break selectors | Gemini extraction + monitoring |
| Address ambiguity | typos, abbreviations, ZIP/county mismatch | normalization + crosswalk + confidence |
| Cost at scale | browsers + LLM | cache, HTTP-first, batch/off-peak |

---

## 7. Stakeholder snippets (copy/paste)

### Boss / executive (30 seconds)

“We’re building an API that takes an address and returns official property/tax fields. The hard part is fragmentation: thousands of government websites. Our approach is a **database registry of portals** plus an engine that **tries the right sources in order** until it succeeds. Coverage scales by **adding registry rows**, not rewriting the product.”

### Customer / business (1 minute)

“Today, bulk property research means humans clicking through many county systems. We automate that: normalize the address, pick the right portals from our registry, scrape with browser automation when required, and extract structured results. If one portal fails, we automatically try the next.”

### Engineer (30 seconds)

“Postgres registry → resolver orders sources by jurisdiction specificity + priority → httpx vs Playwright → Gemini JSON extraction + validation + confidence. Celery for async jobs; Redis cache; Grafana for per-source success rates.”

---

## 8. Phased delivery (example timeline)

| Phase | Weeks | Outcomes |
|------|------|----------|
| 1 | 1–2 | Postgres registry schema + import pipeline + admin edit workflow (minimal) |
| 2 | 3–4 | Normalization + resolver + logging |
| 3 | 5–6 | httpx tier + Playwright tier + Gemini extraction |
| 4 | 7–8 | Public API hardening + caching + evidence store |
| 5 | 9–12 | More vendor connectors + monitoring dashboards |
| 6 | 13+ | Scale rows, anti-bot operations, international expansion |

---

## 9. “Moat” framing (investor-friendly)

The durable asset is the **verified registry + operational playbooks** (what works per vendor/jurisdiction, what breaks, what proxies/throttles are required). Software is the engine; **data operations** is the long-term advantage.

---

## 10. Repo pointers (where this lives in code)

- **DB schema (prototype):** `infrastructure/sql/site_registry.sql`  
- **DB loader:** `core/discovery/site_repository.py`  
- **Resolver (DB + optional YAML):** `core/discovery/source_resolver.py`  
- **Try-sources loop:** `core/orchestration/pipeline.py`  
- **Manager brief:** `explanation.txt`  

---

## 11. Security note (operations)

- Treat **API keys** as secrets: use environment variables, never commit real keys to git.
- Rotate any keys that were ever pasted into chat/logs.

---

*Last updated: aligns with the repo’s DB-first registry approach and the implementation guide you provided.*
