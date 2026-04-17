"""
Load ordered data_sources from PostgreSQL (3000+ site rows).

Set USE_SITE_DATABASE=true and DATABASE_URL. Apply infrastructure/sql/site_registry.sql first.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from core.address.models import NormalizedAddress
from core.discovery.registry import SourceConfig

logger = logging.getLogger(__name__)


def _row_to_source(row: tuple[Any, ...]) -> SourceConfig:
    ds_id, name, scraper, base_url, uid, stype, req_br, dtypes, cfg = row
    cfg = dict(cfg or {})
    cfg["_db_source_id"] = ds_id
    return SourceConfig(
        name=name,
        base_url=base_url,
        scraper=scraper,
        uid=uid,
        search_type=stype or "address",
        requires_browser=bool(req_br),
        data_types=list(dtypes or []),
        params=cfg,
    )


def _fetch_ds_rows(cur, jurisdiction_id: int):
    cur.execute(
        """
        SELECT id, name, scraper, base_url, uid, search_type,
               requires_browser, data_types, config
        FROM data_sources
        WHERE jurisdiction_id = %s AND enabled
        ORDER BY priority ASC, id ASC
        """,
        (jurisdiction_id,),
    )
    return cur.fetchall()


def fetch_sources_for_address(address: NormalizedAddress) -> list[SourceConfig]:
    """Return data_sources for matching jurisdiction(s), municipality-first then county."""
    url = os.getenv("DATABASE_URL")
    if not url or os.getenv("USE_SITE_DATABASE", "").lower() not in ("1", "true", "yes"):
        return []

    try:
        import psycopg
    except ImportError:
        logger.warning("psycopg not installed; pip install 'psycopg[binary]'")
        return []

    country = (address.country or "US").upper()
    state = (address.state or "").upper()
    county = (address.county or "").strip()
    city = (address.city or "").strip()

    out: list[SourceConfig] = []
    seen_ids: set[int] = set()

    def add_rows(rows):
        for row in rows:
            sid = row[0]
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            out.append(_row_to_source(row))

    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                # 1) Municipality + county (most specific)
                if county and city:
                    cur.execute(
                        """
                        SELECT j.id FROM jurisdictions j
                        WHERE j.country_code = %s AND j.state_region = %s
                          AND LOWER(TRIM(j.county)) = LOWER(%s)
                          AND LOWER(TRIM(j.municipality)) = LOWER(%s)
                        LIMIT 1
                        """,
                        (country, state, county, city),
                    )
                    r = cur.fetchone()
                    if r:
                        add_rows(_fetch_ds_rows(cur, r[0]))
                        cur.execute(
                            "SELECT parent_id FROM jurisdictions WHERE id = %s",
                            (r[0],),
                        )
                        pr = cur.fetchone()
                        if pr and pr[0]:
                            add_rows(_fetch_ds_rows(cur, pr[0]))

                # 2) County-wide (municipality empty)
                if county:
                    cur.execute(
                        """
                        SELECT j.id FROM jurisdictions j
                        WHERE j.country_code = %s AND j.state_region = %s
                          AND LOWER(TRIM(j.county)) = LOWER(%s)
                          AND (j.municipality IS NULL OR TRIM(j.municipality) = '')
                        LIMIT 1
                        """,
                        (country, state, county),
                    )
                    r = cur.fetchone()
                    if r:
                        add_rows(_fetch_ds_rows(cur, r[0]))

    except Exception:
        logger.exception("Failed to load data_sources from database")
        return []

    logger.info("Loaded %d data_sources from DB for %s", len(out), address.one_line)
    return out
