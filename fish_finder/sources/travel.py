from __future__ import annotations

import logging

import httpx

from ..cache import TTLCache
from ..disk_cache import PersistentTTLCache
from ..models import Location, TravelInfo, WaterBody
from .base import DataSource

log = logging.getLogger(__name__)

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving"
_TRAVEL_CACHE = TTLCache[str, TravelInfo | None](ttl_seconds=30 * 60, max_entries=512)
_TRAVEL_DISK_CACHE = PersistentTTLCache[dict | None]("travel_info", ttl_seconds=6 * 60 * 60, max_entries=2048)


class TravelSource(DataSource):
    """Calculates driving times via the OSRM public routing API."""

    def fetch(self, *, origin: Location, destination: WaterBody) -> TravelInfo | None:
        cache_key = _travel_cache_key(origin, destination)
        found, cached = _TRAVEL_CACHE.get_with_presence(cache_key)
        if found:
            log.debug("OSRM cache hit for %s", destination.name)
            return cached
        disk_cached = _TRAVEL_DISK_CACHE.get(cache_key)
        if disk_cached is not None:
            info = TravelInfo(**disk_cached)
            _TRAVEL_CACHE.set(cache_key, info)
            log.debug("OSRM disk cache hit for %s", destination.name)
            return info

        coords = f"{origin.lon},{origin.lat};{destination.lon},{destination.lat}"
        try:
            resp = httpx.get(
                f"{OSRM_URL}/{coords}",
                params={"overview": "false"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != "Ok" or not data.get("routes"):
                log.debug("OSRM: no route to %s", destination.name)
                return None

            route = data["routes"][0]
            info = TravelInfo(
                destination=destination,
                duration_minutes=round(route["duration"] / 60, 1),
                distance_km=round(route["distance"] / 1000, 1),
            )
            log.debug(
                "OSRM: %s → %.1f min, %.1f km",
                destination.name, info.duration_minutes, info.distance_km,
            )
            _TRAVEL_CACHE.set(cache_key, info)
            _TRAVEL_DISK_CACHE.set(cache_key, info.model_dump())
            return info
        except (httpx.HTTPError, KeyError, IndexError) as e:
            log.warning("OSRM failed for %s: %s", destination.name, e)
            _TRAVEL_CACHE.set(cache_key, None)
            _TRAVEL_DISK_CACHE.set(cache_key, None)
            return None

    def fetch_batch(
        self,
        origin: Location,
        destinations: list[WaterBody],
    ) -> list[TravelInfo]:
        """Fetch travel info for multiple destinations, skipping failures."""
        log.debug("Calculating travel to %d destinations", len(destinations))
        results: list[TravelInfo] = []
        if not destinations:
            return results

        missing: list[WaterBody] = []
        for dest in destinations:
            cache_key = _travel_cache_key(origin, dest)
            found, mem_value = _TRAVEL_CACHE.get_with_presence(cache_key)
            if found:
                if mem_value is not None:
                    results.append(mem_value)
                continue
            disk_value = _TRAVEL_DISK_CACHE.get(cache_key)
            if disk_value is not None:
                info = TravelInfo(**disk_value)
                _TRAVEL_CACHE.set(cache_key, info)
                results.append(info)
                continue
            missing.append(dest)

        if missing:
            results.extend(self._fetch_batch_table(origin, missing))

        results.sort(key=lambda t: t.duration_minutes)
        log.info("Travel: %d/%d destinations reachable", len(results), len(destinations))
        return results

    def _fetch_batch_table(self, origin: Location, destinations: list[WaterBody]) -> list[TravelInfo]:
        coords = [f"{origin.lon},{origin.lat}"] + [f"{d.lon},{d.lat}" for d in destinations]
        coord_str = ";".join(coords)
        destination_indexes = ";".join(str(i) for i in range(1, len(destinations) + 1))

        try:
            resp = httpx.get(
                f"{OSRM_TABLE_URL}/{coord_str}",
                params={
                    "sources": "0",
                    "destinations": destination_indexes,
                    "annotations": "duration,distance",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            durations = data.get("durations", [[]])[0]
            distances = data.get("distances", [[]])[0]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            log.warning("OSRM table failed, falling back to single-route calls: %s", e)
            return [info for d in destinations if (info := self.fetch(origin=origin, destination=d)) is not None]

        results: list[TravelInfo] = []
        for idx, dest in enumerate(destinations):
            duration_s = durations[idx] if idx < len(durations) else None
            distance_m = distances[idx] if idx < len(distances) else None
            if duration_s is None or distance_m is None:
                cache_key = _travel_cache_key(origin, dest)
                _TRAVEL_CACHE.set(cache_key, None)
                _TRAVEL_DISK_CACHE.set(cache_key, None)
                continue
            info = TravelInfo(
                destination=dest,
                duration_minutes=round(duration_s / 60, 1),
                distance_km=round(distance_m / 1000, 1),
            )
            cache_key = _travel_cache_key(origin, dest)
            _TRAVEL_CACHE.set(cache_key, info)
            _TRAVEL_DISK_CACHE.set(cache_key, info.model_dump())
            log.debug(
                "OSRM(table): %s → %.1f min, %.1f km",
                dest.name, info.duration_minutes, info.distance_km,
            )
            results.append(info)
        return results


def _travel_cache_key(origin: Location, destination: WaterBody) -> str:
    return (
        f"{origin.lat:.5f},{origin.lon:.5f}:"
        f"{destination.lat:.5f},{destination.lon:.5f}"
    )


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Calculate travel time to a water body")
    parser.add_argument("--origin-lat", type=float, required=True)
    parser.add_argument("--origin-lon", type=float, required=True)
    parser.add_argument("--dest-lat", type=float, required=True)
    parser.add_argument("--dest-lon", type=float, required=True)
    parser.add_argument("--dest-name", type=str, default="Destination")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s  %(levelname)-8s  %(message)s")

    source = TravelSource()
    origin = Location(lat=args.origin_lat, lon=args.origin_lon)
    dest = WaterBody(name=args.dest_name, type="unknown", lat=args.dest_lat, lon=args.dest_lon)
    info = source.fetch(origin=origin, destination=dest)
    if info:
        print(f"  {info.destination.name}: {info.duration_minutes} min, {info.distance_km} km")
    else:
        print("  No route found")
