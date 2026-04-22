"""
ArcGIS MapServer/FeatureServer /query scraper (HTTP only — no Playwright).

Registry params:
  layer_url — Layer URL ending in .../MapServer/N or .../FeatureServer/N (no /query).
  preset — Optional schema preset:
    - battle_creek (default): Calhoun County MI / Battle Creek GIS field names
    - harris_hcad: Harris County TX HCAD parcels (gis.hctx.net)
    - maricopa_az: Maricopa County AZ parcels (gis.mcassessor.maricopa.gov)
    - cook_il: Cook County IL hosted parcels (gis.cookcountyil.gov; queries ``name`` / labels)
  max_http_attempts, connect_timeout_s, read_timeout_s — see class defaults.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from core.address.models import NormalizedAddress
from core.scraping.base import BaseScraper
from core.scraping.models import PropertyRecord

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, application/geo+json, text/plain, */*",
}

_SUFFIX_ABBR = {
    "STREET": "ST",
    "AVENUE": "AVE",
    "DRIVE": "DR",
    "ROAD": "RD",
    "LANE": "LN",
    "COURT": "CT",
    "BOULEVARD": "BLVD",
    "PLACE": "PL",
    "CIRCLE": "CIR",
}


def _esc_sql(s: str) -> str:
    return (s or "").replace("'", "''")


def _squash_ws(s: str) -> str:
    return " ".join((s or "").split())


class ArcGISParcelQueryScraper(BaseScraper):
    """Query a public parcel MapServer layer via REST /query."""

    name = "us_arcgis_parcel_query"
    requires_browser = False

    def __init__(
        self,
        headless: bool = True,
        source_params: dict | None = None,
        uid: str | None = None,
    ):
        _ = headless, uid
        self.params = source_params or {}
        self.layer_url = (self.params.get("layer_url") or "").rstrip("/")
        if not self.layer_url:
            raise ValueError("us_arcgis_parcel_query requires params.layer_url in the registry")
        self._preset = (self.params.get("preset") or "battle_creek").strip().lower()
        self._max_http_attempts = max(1, int(self.params.get("max_http_attempts", 3)))
        self._connect_timeout = float(self.params.get("connect_timeout_s", 20.0))
        self._read_timeout = float(self.params.get("read_timeout_s", 90.0))

    async def close(self) -> None:
        return

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._connect_timeout,
            read=self._read_timeout,
            write=30.0,
            pool=30.0,
        )

    async def health_check(self) -> bool:
        url = f"{self.layer_url}?f=json"
        try:
            async with httpx.AsyncClient(
                headers=DEFAULT_HEADERS,
                timeout=self._timeout(),
            ) as client:
                r = await client.get(url)
                return r.status_code == 200 and ("Feature Layer" in r.text or '"type"' in r.text)
        except httpx.HTTPError:
            return False

    # --- WHERE clause builders by preset ---

    def _where_clauses(self, address: NormalizedAddress) -> list[str]:
        if self._preset == "harris_hcad":
            return self._where_harris_hcad(address)
        if self._preset == "maricopa_az":
            return self._where_maricopa_az(address)
        if self._preset == "cook_il":
            return self._where_cook_il(address)
        return self._where_battle_creek(address)

    def _where_battle_creek(self, address: NormalizedAddress) -> list[str]:
        num = (address.street_number or "").strip()
        name = (address.street_name or "").strip().upper()
        name_e = _esc_sql(name)
        suf = (address.street_suffix or "").strip().upper()
        suf_abbr = _SUFFIX_ABBR.get(suf, "")
        clauses: list[str] = []
        seen: set[str] = set()

        def add(w: str) -> None:
            if w and w not in seen:
                seen.add(w)
                clauses.append(w)

        if num and name:
            add(f"PSSNUMBER = '{_esc_sql(num)}' AND UPPER(PSSSTREET) LIKE '%{name_e}%'")
            if suf_abbr:
                add(
                    f"PSSNUMBER = '{_esc_sql(num)}' AND UPPER(PSSSTREET) LIKE '%{name_e} {suf_abbr}%'"
                )
            add(f"UPPER(OTADDRESS) LIKE '%{_esc_sql(num)}%{name_e}%'")
            if suf_abbr:
                add(f"UPPER(OTADDRESS) LIKE '%{_esc_sql(num)}%{name_e} {suf_abbr}%'")

        fs = _squash_ws((address.full_street or "").strip().upper())
        if len(fs) >= 4:
            add(f"UPPER(OTADDRESS) LIKE '%{_esc_sql(fs)}%'")

        one = _squash_ws((address.one_line or "").replace(",", " ").strip().upper())
        if len(one) >= 8 and one != fs:
            add(f"UPPER(OTADDRESS) LIKE '%{_esc_sql(one)}%'")

        return clauses

    def _where_harris_hcad(self, address: NormalizedAddress) -> list[str]:
        """HCAD Parcels layer: site_str_num (int), site_str_name, site_zip, site_city."""
        num = (address.street_number or "").strip()
        name = (address.street_name or "").strip().upper()
        name_e = _esc_sql(name)
        city = (address.city or "").strip().upper()
        city_e = _esc_sql(city)
        zip5 = (address.zip_code or "")[:5]
        zip_e = _esc_sql(zip5)
        clauses: list[str] = []
        seen: set[str] = set()

        def add(w: str) -> None:
            if w and w not in seen:
                seen.add(w)
                clauses.append(w)

        try:
            n_int = int(num)
        except ValueError:
            n_int = None

        if n_int is not None and name:
            add(f"site_str_num = {n_int} AND UPPER(site_str_name) LIKE '%{name_e}%'")
        if n_int is not None and name and city:
            add(
                f"site_str_num = {n_int} AND UPPER(site_str_name) LIKE '%{name_e}%' "
                f"AND UPPER(site_city) LIKE '%{city_e}%'"
            )
        if name and zip5:
            add(f"UPPER(site_str_name) LIKE '%{name_e}%' AND site_zip LIKE '{zip_e}%'")
        if name and city:
            add(f"UPPER(site_str_name) LIKE '%{name_e}%' AND UPPER(site_city) LIKE '%{city_e}%'")
        return clauses

    def _where_maricopa_az(self, address: NormalizedAddress) -> list[str]:
        """Maricopa Parcels: PHYSICAL_STREET_NUM (string), PHYSICAL_STREET_NAME, PHYSICAL_ADDRESS."""
        num = _esc_sql((address.street_number or "").strip())
        name = _esc_sql((address.street_name or "").strip().upper())
        city = _esc_sql((address.city or "").strip().upper())
        zip5 = _esc_sql((address.zip_code or "")[:5])
        clauses: list[str] = []
        seen: set[str] = set()

        def add(w: str) -> None:
            if w and w not in seen:
                seen.add(w)
                clauses.append(w)

        if num and name:
            add(
                f"PHYSICAL_STREET_NUM = '{num}' AND UPPER(PHYSICAL_STREET_NAME) LIKE '%{name}%'"
            )
            add(f"UPPER(PHYSICAL_ADDRESS) LIKE '%{num}%{name}%'")
        if num and name and city:
            add(
                f"PHYSICAL_STREET_NUM = '{num}' AND UPPER(PHYSICAL_STREET_NAME) LIKE '%{name}%' "
                f"AND UPPER(PHYSICAL_CITY) LIKE '%{city}%'"
            )
        if zip5 and name:
            add(f"PHYSICAL_ZIP LIKE '{zip5}%' AND UPPER(PHYSICAL_STREET_NAME) LIKE '%{name}%'")
        pa = _squash_ws((address.one_line or "").replace(",", " ").strip().upper())
        if len(pa) >= 8:
            add(f"UPPER(PHYSICAL_ADDRESS) LIKE '%{_esc_sql(pa)}%'")
        return clauses

    def _where_cook_il(self, address: NormalizedAddress) -> list[str]:
        """Cook County hosted parcel layer uses ``name`` as display/search label (see layer metadata)."""
        num = (address.street_number or "").strip()
        name = (address.street_name or "").strip().upper()
        name_e = _esc_sql(name)
        dir_ = (address.street_direction or "").strip().upper()
        dir_e = _esc_sql(dir_) if dir_ else ""
        clauses: list[str] = []
        seen: set[str] = set()

        def add(w: str) -> None:
            if w and w not in seen:
                seen.add(w)
                clauses.append(w)

        if num and name:
            add(f"UPPER(name) LIKE '%{_esc_sql(num)}%{name_e}%'")
            if dir_e:
                add(f"UPPER(name) LIKE '%{_esc_sql(num)} {dir_e}%{name_e}%'")
        fs = _squash_ws((address.full_street or "").strip().upper())
        if len(fs) >= 5:
            add(f"UPPER(name) LIKE '%{_esc_sql(fs)}%'")
        one = _squash_ws((address.one_line or "").replace(",", " ").strip().upper())
        if len(one) >= 8 and one != fs:
            add(f"UPPER(name) LIKE '%{_esc_sql(one)}%'")
        if num and name and (address.city or "").strip():
            city_e = _esc_sql((address.city or "").strip().upper())
            add(f"UPPER(name) LIKE '%{city_e}%' AND UPPER(name) LIKE '%{_esc_sql(num)}%' AND UPPER(name) LIKE '%{name_e}%'")
        return clauses

    def _pick_feature(
        self, features: list[dict], address: NormalizedAddress
    ) -> dict | None:
        if not features:
            return None
        if self._preset == "harris_hcad":
            return self._pick_harris(features, address)
        if self._preset == "maricopa_az":
            return self._pick_maricopa(features, address)
        if self._preset == "cook_il":
            return self._pick_cook_il(features, address)
        return self._pick_battle_creek(features, address)

    @staticmethod
    def _pick_battle_creek(features: list[dict], address: NormalizedAddress) -> dict | None:
        if not features:
            return None
        zip5 = (address.zip_code or "")[:5]
        city = (address.city or "").strip().upper()
        if zip5:
            for f in features:
                rzip = (f.get("attributes") or {}).get("RSTATEZIP") or ""
                if zip5 in str(rzip):
                    return f
        if city:
            for f in features:
                rzip = (f.get("attributes") or {}).get("RSTATEZIP") or ""
                ot = (f.get("attributes") or {}).get("OTADDRESS") or ""
                if city in str(ot).upper() or city in str(rzip).upper():
                    return f
        return features[0]

    @staticmethod
    def _pick_harris(features: list[dict], address: NormalizedAddress) -> dict | None:
        zip5 = (address.zip_code or "")[:5]
        city = (address.city or "").strip().upper()
        for f in features:
            a = f.get("attributes") or {}
            if zip5 and zip5 in str(a.get("site_zip") or ""):
                return f
        for f in features:
            a = f.get("attributes") or {}
            if city and str(a.get("site_city") or "").upper().find(city) >= 0:
                return f
        return features[0]

    @staticmethod
    def _pick_maricopa(features: list[dict], address: NormalizedAddress) -> dict | None:
        zip5 = (address.zip_code or "")[:5]
        city = (address.city or "").strip().upper()
        for f in features:
            a = f.get("attributes") or {}
            if zip5 and zip5 in str(a.get("PHYSICAL_ZIP") or ""):
                return f
        for f in features:
            a = f.get("attributes") or {}
            if city and city in str(a.get("PHYSICAL_CITY") or "").upper():
                return f
        return features[0]

    @staticmethod
    def _pick_cook_il(features: list[dict], address: NormalizedAddress) -> dict | None:
        if not features:
            return None
        num = (address.street_number or "").strip()
        name = (address.street_name or "").strip().upper()
        city = (address.city or "").strip().upper()
        for f in features:
            a = f.get("attributes") or {}
            nm = str(a.get("name") or "").upper()
            if num and num in nm and name and name in nm:
                if not city or city in nm:
                    return f
        return features[0]

    def _attrs_to_record(
        self,
        attrs: dict[str, Any],
        address: NormalizedAddress,
        source_url: str,
    ) -> PropertyRecord:
        if self._preset == "harris_hcad":
            return self._record_harris(attrs, address, source_url)
        if self._preset == "maricopa_az":
            return self._record_maricopa(attrs, address, source_url)
        if self._preset == "cook_il":
            return self._record_cook_il(attrs, address, source_url)
        return self._record_battle_creek(attrs, address, source_url)

    @staticmethod
    def _record_battle_creek(
        attrs: dict[str, Any],
        address: NormalizedAddress,
        source_url: str,
    ) -> PropertyRecord:
        owner = (attrs.get("ONERNAME1") or "").strip()
        n2 = (attrs.get("ONERNAME2") or "").strip()
        if n2 and n2.lower() not in owner.lower():
            owner = f"{owner}; {n2}".strip("; ") if owner else n2

        ot = (attrs.get("OTADDRESS") or "").strip()
        rz = (attrs.get("RSTATEZIP") or "").strip()
        prop_addr = ", ".join(p for p in (ot, rz) if p) or address.one_line

        parcel = attrs.get("PARCEL_ID") or attrs.get("PIN") or attrs.get("parcel_id")

        acres = attrs.get("ACRES")
        acreage: float | None = None
        if acres is not None:
            try:
                acreage = float(acres)
            except (TypeError, ValueError):
                pass

        return PropertyRecord(
            parcel_number=str(parcel) if parcel else None,
            owner_name=owner or None,
            property_address=prop_addr,
            acreage=acreage,
            source_url=source_url,
            source_name=ArcGISParcelQueryScraper.name,
            scraped_at=datetime.now(timezone.utc),
            confidence=0.72,
            raw_html=str(attrs),
        )

    @staticmethod
    def _record_harris(
        attrs: dict[str, Any],
        address: NormalizedAddress,
        source_url: str,
    ) -> PropertyRecord:
        o1 = (attrs.get("owner_name_1") or "").strip()
        o2 = (attrs.get("owner_name_2") or "").strip()
        o3 = (attrs.get("owner_name_3") or "").strip()
        parts = [p for p in (o1, o2, o3) if p]
        owner = "; ".join(parts) if parts else None

        num = attrs.get("site_str_num")
        street = (attrs.get("site_str_name") or "").strip()
        sfx = (attrs.get("site_str_sfx") or "").strip()
        city = (attrs.get("site_city") or "").strip()
        z = (attrs.get("site_zip") or "").strip()
        line = " ".join(str(p) for p in (num, street, sfx, city, z) if p)
        prop_addr = line or address.one_line

        parcel = attrs.get("HCAD_NUM") or attrs.get("LOWPARCELID")
        assessed = attrs.get("total_market_val") or attrs.get("total_appraised_val")
        av: float | None = None
        if assessed is not None:
            try:
                av = float(assessed)
            except (TypeError, ValueError):
                pass

        ac: float | None = None
        if attrs.get("acreage_1") is not None:
            try:
                ac = float(attrs["acreage_1"])
            except (TypeError, ValueError):
                pass

        return PropertyRecord(
            parcel_number=str(parcel).strip() if parcel else None,
            owner_name=owner,
            property_address=prop_addr,
            assessed_value=av,
            acreage=ac,
            legal_description=(attrs.get("legal_dscr_1") or "").strip() or None,
            source_url=source_url,
            source_name=ArcGISParcelQueryScraper.name,
            scraped_at=datetime.now(timezone.utc),
            confidence=0.73,
            raw_html=str(attrs),
        )

    @staticmethod
    def _record_maricopa(
        attrs: dict[str, Any],
        address: NormalizedAddress,
        source_url: str,
    ) -> PropertyRecord:
        owner = (attrs.get("OWNER_NAME") or "").strip() or None
        prop_addr = (attrs.get("PHYSICAL_ADDRESS") or "").strip() or address.one_line
        parcel = attrs.get("APN") or attrs.get("APN_DASH")

        def _floatish(v: Any) -> float | None:
            if v is None or v == "":
                return None
            try:
                return float(str(v).replace(",", ""))
            except ValueError:
                return None

        return PropertyRecord(
            parcel_number=str(parcel).strip() if parcel else None,
            owner_name=owner,
            property_address=prop_addr,
            assessed_value=_floatish(attrs.get("FCV_CUR")),
            taxable_value=_floatish(attrs.get("LPV_CUR")),
            source_url=source_url,
            source_name=ArcGISParcelQueryScraper.name,
            scraped_at=datetime.now(timezone.utc),
            confidence=0.73,
            raw_html=str(attrs),
        )

    @staticmethod
    def _record_cook_il(
        attrs: dict[str, Any],
        address: NormalizedAddress,
        source_url: str,
    ) -> PropertyRecord:
        """Cook hosted parcel layer: display field ``name``; PIN-style ids vary by release."""
        nm = (attrs.get("name") or "").strip()
        leaf = (attrs.get("flyleaf_title") or "").strip()
        prop_addr = nm or leaf or address.one_line
        parts = [
            str(attrs[k])
            for k in ("map_id", "plan_number", "fly_id")
            if attrs.get(k) is not None and str(attrs.get(k)).strip() != ""
        ]
        parcel = ".".join(parts) if parts else None
        if not parcel and attrs.get("OBJECTID") is not None:
            parcel = f"objectid:{attrs['OBJECTID']}"

        return PropertyRecord(
            parcel_number=parcel,
            property_address=prop_addr,
            legal_description=leaf or None,
            source_url=source_url,
            source_name=ArcGISParcelQueryScraper.name,
            scraped_at=datetime.now(timezone.utc),
            confidence=0.68,
            raw_html=str(attrs),
        )

    async def _get_query_json(
        self,
        client: httpx.AsyncClient,
        query_url: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        last_err: Exception | None = None
        for attempt in range(1, self._max_http_attempts + 1):
            try:
                r = await client.get(query_url, params=params)
                if r.status_code in (502, 503, 504) and attempt < self._max_http_attempts:
                    wait = 1.5 * attempt
                    logger.info(
                        "ArcGIS HTTP %s on attempt %d/%d; retry in %.1fs",
                        r.status_code,
                        attempt,
                        self._max_http_attempts,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                try:
                    return r.json()
                except json.JSONDecodeError as e:
                    snippet = (r.text or "")[:200].replace("\n", " ")
                    logger.warning(
                        "ArcGIS returned non-JSON (attempt %d): %s: body=%r",
                        attempt,
                        e,
                        snippet,
                    )
                    last_err = e
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_err = e
                wait = 1.0 * (2 ** (attempt - 1))
                logger.warning(
                    "ArcGIS network error %s: %s (attempt %d/%d)%s",
                    type(e).__name__,
                    e,
                    attempt,
                    self._max_http_attempts,
                    f"; retry in {wait:.1f}s" if attempt < self._max_http_attempts else "",
                )
                if attempt < self._max_http_attempts:
                    await asyncio.sleep(wait)
                    continue
            except httpx.HTTPStatusError as e:
                last_err = e
                logger.warning(
                    "ArcGIS HTTP %s: %s",
                    e.response.status_code if e.response else "?",
                    e,
                )
                break
            except Exception as e:
                last_err = e
                logger.warning("ArcGIS unexpected error: %s: %s", type(e).__name__, e)
                break

        if last_err:
            logger.warning("ArcGIS query gave up after retries: %s: %s", type(last_err).__name__, last_err)
        return None

    async def scrape(self, address: NormalizedAddress) -> PropertyRecord | None:
        clauses = self._where_clauses(address)
        if not clauses:
            logger.warning("ArcGIS parcel query: no WHERE clauses for %s", address.one_line)
            return None

        query_url = f"{self.layer_url}/query"
        http_ok_no_match = 0

        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=self._timeout(),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
        ) as client:
            for where in clauses:
                params = {
                    "f": "json",
                    "where": where,
                    "outFields": "*",
                    "returnGeometry": "false",
                    "outSR": "4326",
                }
                data = await self._get_query_json(client, query_url, params)
                if data is None:
                    continue

                err = data.get("error")
                if err:
                    logger.warning("ArcGIS server error object: %s", err)
                    continue

                feats = data.get("features") or []
                if not feats:
                    http_ok_no_match += 1
                    logger.debug("ArcGIS 0 features for WHERE %s", where[:100])
                    continue

                feat = self._pick_feature(feats, address)
                if not feat:
                    continue
                attrs = feat.get("attributes") or {}
                q = urlencode({"f": "json", "where": where, "outFields": "*", "returnGeometry": "false"})
                src = f"{query_url}?{q}"
                return self._attrs_to_record(attrs, address, src)

        if http_ok_no_match:
            logger.info(
                "ArcGIS: server responded but no parcel matched (%d clause(s) returned 0 rows) for %s",
                http_ok_no_match,
                address.one_line,
            )
        else:
            logger.info("ArcGIS parcel query: no matching features for %s", address.one_line)
        return None
