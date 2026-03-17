from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from ..disk_cache import PersistentTTLCache
from ..models import Location, Permit, WaterBody
from ..utils import haversine_km
from . import overpass
from .base import DataSource

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "fish-finder/1.0"
NOMINATIM_QUERIES = ["fishing lake", "fishery", "angling club", "fishing pond"]
_WATERS_DISK_CACHE = PersistentTTLCache[list[dict]]("waters_results", ttl_seconds=2 * 60 * 60, max_entries=128)


class WatersSource(DataSource):
    """Finds water bodies and fisheries near a location via Overpass and Nominatim."""

    def fetch(
        self,
        *,
        location: Location,
        radius_m: int = 30_000,
        permits: list[Permit] | None = None,
    ) -> list[WaterBody]:
        cache_key = _waters_cache_key(location, radius_m, permits or [])
        cached = _WATERS_DISK_CACHE.get(cache_key)
        if cached is not None:
            waters = [WaterBody(**w) for w in cached]
            log.debug("Waters disk cache hit (%d results)", len(waters))
            return waters

        overpass_waters = self._fetch_overpass(location, radius_m)
        nominatim_waters = self._fetch_nominatim(location, radius_m)

        merged = _merge_waters(overpass_waters, nominatim_waters)

        for w in merged:
            w.access = _classify_access(w)

        before = len(merged)
        merged = _filter_by_access(merged, permits or [])
        merged.sort(key=lambda w: w.distance_km)

        log.info(
            "Found %d water bodies within %dm of (%.4f, %.4f) "
            "(%d overpass, %d nominatim before dedup, %d removed by access filter)",
            len(merged), radius_m, location.lat, location.lon,
            len(overpass_waters), len(nominatim_waters), before - len(merged),
        )
        _WATERS_DISK_CACHE.set(cache_key, [w.model_dump() for w in merged])
        return merged

    def _fetch_overpass(self, location: Location, radius_m: int) -> list[WaterBody]:
        queries = _build_queries(location, radius_m)
        all_elements: list[dict] = []
        with ThreadPoolExecutor(max_workers=max(1, len(queries))) as pool:
            futures = [
                pool.submit(
                    overpass.query,
                    ql,
                    max_retries=1,
                    request_timeout=12.0,
                    retry_delay=1.0,
                )
                for ql in queries
            ]
            for future in as_completed(futures):
                try:
                    all_elements.extend(future.result())
                except ConnectionError:
                    log.warning("Overpass query failed, continuing with partial results")
        return _parse_elements(all_elements, location)

    def _fetch_nominatim(self, location: Location, radius_m: int) -> list[WaterBody]:
        """Supplementary text search for fishing venues via Nominatim."""
        deg_offset = radius_m / 111_000
        viewbox = (
            f"{location.lon - deg_offset},{location.lat + deg_offset},"
            f"{location.lon + deg_offset},{location.lat - deg_offset}"
        )

        waters: list[WaterBody] = []
        with ThreadPoolExecutor(max_workers=min(4, len(NOMINATIM_QUERIES))) as pool:
            futures = [
                pool.submit(self._search_nominatim_query, q, viewbox)
                for q in NOMINATIM_QUERIES
            ]
            raw_results: list[dict] = []
            for future in as_completed(futures):
                raw_results.extend(future.result())

        seen_names: set[str] = set()
        for r in raw_results:
            name = r.get("display_name", "").split(",")[0].strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (KeyError, ValueError):
                continue

            dist = haversine_km(location.lat, location.lon, lat, lon)
            if dist > radius_m / 1000:
                continue

            waters.append(WaterBody(
                name=name,
                type=r.get("type", "fishing"),
                lat=lat,
                lon=lon,
                distance_km=round(dist, 1),
                tags={"source": "nominatim", "class": r.get("class", "")},
            ))

        log.debug("Nominatim returned %d unique fishing venues", len(waters))
        return waters

    def _search_nominatim_query(self, query: str, viewbox: str) -> list[dict]:
        try:
            resp = httpx.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "json",
                    "limit": 20,
                    "viewbox": viewbox,
                    "bounded": 1,
                },
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=8.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Nominatim search '%s' failed: %s", query, e)
        return []


_NON_WATER_TAGS = {
    "shop", "amenity", "highway", "office", "tourism",
    "craft", "building", "railway",
}

_ALLOWED_AMENITIES = {"fountain"}


def _is_water_feature(tags: dict[str, str]) -> bool:
    """Return True if the element is plausibly a fishable water body or fishing venue."""
    if any(tags.get(t) for t in ("natural", "water", "waterway", "landuse")):
        return True
    if tags.get("leisure") == "fishing" or tags.get("sport") == "fishing":
        return True
    if tags.get("fishing") == "yes":
        return True

    for tag_key in _NON_WATER_TAGS:
        val = tags.get(tag_key, "")
        if val and val not in _ALLOWED_AMENITIES:
            return False

    return True


def _parse_elements(elements: list[dict[str, Any]], location: Location) -> list[WaterBody]:
    """Convert raw Overpass elements into WaterBody objects."""
    waters: list[WaterBody] = []
    seen: set[str] = set()

    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if not lat or not lon:
            continue

        tags = el.get("tags", {})

        if not _is_water_feature(tags):
            continue

        name = tags.get("name", "")

        water_type = (
            tags.get("leisure")
            or tags.get("sport")
            or tags.get("water")
            or tags.get("waterway")
            or tags.get("natural")
            or tags.get("landuse")
            or "unknown"
        )

        if not name:
            name = tags.get("description", "")
        if not name:
            continue

        dedup_key = name.lower().strip()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        waters.append(
            WaterBody(
                name=name,
                type=water_type,
                lat=lat,
                lon=lon,
                distance_km=round(haversine_km(location.lat, location.lon, lat, lon), 1),
                tags=tags,
            )
        )

    return waters


def _merge_waters(
    overpass_waters: list[WaterBody],
    nominatim_waters: list[WaterBody],
) -> list[WaterBody]:
    """Combine results from both sources, deduplicating by name or proximity."""
    merged = list(overpass_waters)
    existing_names = {w.name.lower().strip() for w in merged}

    for nw in nominatim_waters:
        if nw.name.lower().strip() in existing_names:
            continue
        if any(haversine_km(nw.lat, nw.lon, ew.lat, ew.lon) < 0.15 for ew in merged):
            continue
        merged.append(nw)

    return merged


def _build_queries(loc: Location, radius_m: int) -> list[str]:
    """Return two Overpass queries: one fishing-specific, one for general water bodies.

    Split to keep each query under the Overpass timeout. Uses bbox instead of
    around for efficiency.
    """
    deg_offset = radius_m / 111_000
    bbox = f"{loc.lat - deg_offset},{loc.lon - deg_offset},{loc.lat + deg_offset},{loc.lon + deg_offset}"

    fishing_query = f"""
[out:json][timeout:25];
(
  node["leisure"="fishing"]({bbox});
  way["leisure"="fishing"]({bbox});
  relation["leisure"="fishing"]({bbox});
  node["sport"="fishing"]({bbox});
  way["sport"="fishing"]({bbox});
  relation["sport"="fishing"]({bbox});
  node["fishing"="yes"]({bbox});
  way["fishing"="yes"]({bbox});
  node["name"~"fishing|angling|fishery",i]({bbox});
  way["name"~"fishing|angling|fishery",i]({bbox});
  relation["name"~"fishing|angling|fishery",i]({bbox});
);
out center 200;
"""

    water_query = f"""
[out:json][timeout:25];
(
  node["natural"="water"]["name"]({bbox});
  way["natural"="water"]["name"]({bbox});
  relation["natural"="water"]["name"]({bbox});
  way["water"]["name"]({bbox});
  way["waterway"="canal"]["name"]({bbox});
  way["waterway"="river"]["name"]({bbox});
  way["water"="reservoir"]({bbox});
  relation["water"="reservoir"]({bbox});
  way["landuse"="reservoir"]["name"]({bbox});
  way["water"="lake"]({bbox});
  relation["water"="lake"]({bbox});
);
out center 200;
"""

    return [fishing_query, water_query]


_PRIVATE_NAME_PATTERNS = re.compile(
    r"\b(angling\s+club|angling\s+society|fishing\s+club|fishing\s+society|"
    r"syndicate|members\s+only|private\s+fishing|private\s+lake)\b",
    re.IGNORECASE,
)


def _classify_access(water: WaterBody) -> str:
    """Determine access level from OSM tags and name heuristics."""
    tags = water.tags
    access_tag = tags.get("access", "").lower()

    if access_tag in ("private", "no"):
        return "private"
    if access_tag in ("members", "members_only"):
        return "members_only"
    if access_tag == "permit":
        return "permit_required"
    if access_tag in ("yes", "public", "permissive"):
        return "public"

    if tags.get("club") in ("yes", "fishing", "angling", "sport"):
        return "members_only"

    if _PRIVATE_NAME_PATTERNS.search(water.name):
        return "members_only"

    return "unknown"


def _filter_by_access(
    waters: list[WaterBody],
    permits: list[Permit],
) -> list[WaterBody]:
    """Remove private/members-only waters unless the user holds a matching permit."""
    result: list[WaterBody] = []
    for w in waters:
        if w.access in ("public", "unknown"):
            result.append(w)
        elif w.access in ("permit_required", "members_only", "private"):
            if _permit_matches(w, permits):
                result.append(w)
            else:
                log.debug("Filtered out %s (access=%s, no matching permit)", w.name, w.access)
        else:
            result.append(w)
    return result


_MATCH_STOP_WORDS = frozenset({
    "the", "of", "and", "at", "in", "on", "by", "for", "to", "a",
    "fishing", "angling", "fish", "club", "society", "syndicate",
    "lake", "pond", "river", "canal", "reservoir", "water", "stream",
    "north", "south", "east", "west", "upper", "lower", "new", "old", "great",
    "london", "park", "green", "hill", "field", "wood", "bridge",
})


def _permit_matches(water: WaterBody, permits: list[Permit]) -> bool:
    """Check if any permit plausibly covers a water body via keyword overlap."""
    water_lower = water.name.lower()
    water_type = water.type.lower()
    for permit in permits:
        covers_lower = permit.covers.lower()
        name_lower = permit.name.lower()
        if water_lower in covers_lower or covers_lower in water_lower:
            return True
        if water_lower in name_lower or name_lower in water_lower:
            return True
        water_words = set(re.findall(r"\w+", water_lower)) - _MATCH_STOP_WORDS
        covers_words = set(re.findall(r"\w+", covers_lower + " " + name_lower)) - _MATCH_STOP_WORDS
        if water_words & covers_words:
            return True
        if water_type in ("canal", "waterway") and "canal" in covers_lower:
            return True
    return False


def _waters_cache_key(location: Location, radius_m: int, permits: list[Permit]) -> str:
    permit_key = "|".join(
        sorted(f"{p.name.strip().lower()}::{p.covers.strip().lower()}" for p in permits)
    )
    return f"{location.lat:.4f},{location.lon:.4f}:{radius_m}:{permit_key}"


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s  %(levelname)-8s  %(message)s")

    parser = argparse.ArgumentParser(description="Find water bodies near a location")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius", type=int, default=30_000, help="Search radius in metres")
    args = parser.parse_args()

    source = WatersSource()
    waters = source.fetch(location=Location(lat=args.lat, lon=args.lon), radius_m=args.radius)
    for w in waters:
        print(f"  {w.name} ({w.type}) — {w.distance_km} km")
    print(f"\n  Total: {len(waters)} water bodies")
