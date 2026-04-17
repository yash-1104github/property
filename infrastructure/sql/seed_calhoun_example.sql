-- Example: load Calhoun County + BS&A sources after schema is applied.
-- Run with: psql "$DATABASE_URL" -f infrastructure/sql/site_registry.sql
--           psql "$DATABASE_URL" -f infrastructure/sql/seed_calhoun_example.sql

INSERT INTO jurisdictions (country_code, state_region, county, municipality, slug, display_name)
VALUES ('US', 'MI', 'Calhoun', NULL, 'us_mi_calhoun', 'Calhoun County, MI')
RETURNING id;

-- Replace :jid below with the id returned above, or use:
-- INSERT INTO data_sources (...) SELECT ... FROM jurisdictions WHERE slug = 'us_mi_calhoun';

INSERT INTO data_sources (
    jurisdiction_id, name, scraper, base_url, uid, priority, requires_browser,
    data_types, config
)
SELECT j.id,
       'BS&A — Calhoun County Equalization (NETR)',
       'us_michigan_bsa_online',
       'https://bsaonline.com',
       '662',
       10,
       true,
       ARRAY['tax','parcel','valuation']::text[],
       '{"netronline_directory_url": "https://publicrecords.netronline.com/state/MI/county/calhoun"}'::jsonb
FROM jurisdictions j
WHERE j.slug = 'us_mi_calhoun';

INSERT INTO data_sources (
    jurisdiction_id, name, scraper, base_url, uid, priority, requires_browser,
    data_types, config
)
SELECT j.id,
       'BS&A — Bedford Charter Township',
       'us_michigan_bsa_online',
       'https://bsaonline.com',
       '995',
       20,
       true,
       ARRAY['tax','parcel','valuation']::text[],
       '{}'::jsonb
FROM jurisdictions j
WHERE j.slug = 'us_mi_calhoun';
