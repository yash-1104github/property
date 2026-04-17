-- Site registry for 3000+ property-data portals (assessor / tax / GIS).
-- Resolve: normalize address → jurisdiction row(s) → data_sources ordered by priority → try each until scrape succeeds.
--
-- "Recursive" here means: walk jurisdiction hierarchy (township → county → state) and
-- source priority within each tier — not unbounded crawling.

CREATE TABLE IF NOT EXISTS jurisdictions (
    id              SERIAL PRIMARY KEY,
    country_code    CHAR(2) NOT NULL,
    state_region    VARCHAR(16) NOT NULL,
    county          VARCHAR(128),
    municipality    VARCHAR(128),
    slug            VARCHAR(256) NOT NULL,
    parent_id       INTEGER REFERENCES jurisdictions (id) ON DELETE SET NULL,
    display_name    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_jurisdiction_slug UNIQUE (slug)
);

CREATE INDEX IF NOT EXISTS idx_jurisdictions_lookup
    ON jurisdictions (country_code, state_region, county);

CREATE INDEX IF NOT EXISTS idx_jurisdictions_parent
    ON jurisdictions (parent_id);

COMMENT ON TABLE jurisdictions IS 'Geographic scope for one or more data_sources (e.g. US / MI / Calhoun).';

CREATE TABLE IF NOT EXISTS data_sources (
    id                  SERIAL PRIMARY KEY,
    jurisdiction_id   INTEGER NOT NULL REFERENCES jurisdictions (id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    scraper             VARCHAR(64) NOT NULL,
    base_url            TEXT NOT NULL,
    uid                 VARCHAR(64),
    search_type         VARCHAR(32) NOT NULL DEFAULT 'address',
    priority            INTEGER NOT NULL DEFAULT 100,
    enabled             BOOLEAN NOT NULL DEFAULT true,
    requires_browser    BOOLEAN NOT NULL DEFAULT false,
    data_types          TEXT[] DEFAULT '{}',
    config              JSONB NOT NULL DEFAULT '{}',
    notes               TEXT,
    last_verified       DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_data_sources_jurisdiction_priority
    ON data_sources (jurisdiction_id, priority ASC, id ASC);

COMMENT ON COLUMN data_sources.priority IS 'Lower number = tried earlier when iterating until success.';
COMMENT ON COLUMN data_sources.config IS 'Scraper-specific JSON (e.g. BS&A params, FetchGIS currentMap).';

-- Optional: audit trail for debugging which site succeeded for which address.
CREATE TABLE IF NOT EXISTS source_attempts (
    id              BIGSERIAL PRIMARY KEY,
    request_id      UUID,
    source_id       INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    address_raw     TEXT NOT NULL,
    address_key     VARCHAR(128),
    success         BOOLEAN NOT NULL DEFAULT false,
    error_message   TEXT,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_source_attempts_address_key
    ON source_attempts (address_key, created_at DESC);
