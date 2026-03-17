from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from ..cache import TTLCache
from ..disk_cache import PersistentTTLCache
from ..models import Location, TransitLeg, TransitRoute, WaterBody
from .base import DataSource

log = logging.getLogger(__name__)

TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"
MAX_WORKERS = 6
_TRANSIT_CACHE = TTLCache[str, TransitRoute | None](ttl_seconds=30 * 60, max_entries=512)
_TRANSIT_DISK_CACHE = PersistentTTLCache[dict]("transit_route", ttl_seconds=3 * 60 * 60, max_entries=2048)


class TransitSource(DataSource):
    """Plans public transport routes via the TfL Journey Planner API.

    Uses specific departure date/time for accurate scheduling.
    Free API, no key required (rate-limited).
    """

    def fetch(
        self,
        *,
        origin: Location,
        destination: WaterBody,
        date: str,
        time: str,
    ) -> TransitRoute | None:
        """Find a transit route departing at the given date (YYYY-MM-DD) and time (HH:MM)."""
        cache_key = (
            f"{origin.lat:.5f},{origin.lon:.5f}:"
            f"{destination.lat:.5f},{destination.lon:.5f}:{date}:{time}"
        )
        found, cached = _TRANSIT_CACHE.get_with_presence(cache_key)
        if found:
            log.debug("TfL cache hit for %s", destination.name)
            return cached
        disk_cached = _TRANSIT_DISK_CACHE.get(cache_key)
        if disk_cached is not None:
            route = TransitRoute(**disk_cached)
            _TRANSIT_CACHE.set(cache_key, route)
            log.debug("TfL disk cache hit for %s", destination.name)
            return route

        from_str = f"{origin.lat},{origin.lon}"
        to_str = f"{destination.lat},{destination.lon}"
        tfl_date = date.replace("-", "")
        tfl_time = time.replace(":", "")

        try:
            log.debug("TfL journey: %s → %s on %s at %s", from_str, destination.name, date, time)
            resp = httpx.get(
                f"{TFL_JOURNEY_URL}/{from_str}/to/{to_str}",
                params={
                    "date": tfl_date,
                    "time": tfl_time,
                    "timeIs": "Departing",
                    "mode": "tube,national-rail,overground,elizabeth-line,bus,walking",
                    "nationalSearch": "true",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            log.warning("TfL API failed for %s: %s", destination.name, e)
            _TRANSIT_CACHE.set(cache_key, None)
            return None

        journeys = data.get("journeys", [])
        if not journeys:
            log.debug("TfL: no route to %s", destination.name)
            _TRANSIT_CACHE.set(cache_key, None)
            return None

        journey = journeys[0]

        legs: list[TransitLeg] = []
        for leg in journey.get("legs", []):
            mode_name = leg.get("mode", {}).get("name", "unknown")
            instruction = leg.get("instruction", {})
            summary = instruction.get("summary", "") or instruction.get("detailed", "") or mode_name
            duration = leg.get("duration", 0)
            legs.append(TransitLeg(mode=mode_name, summary=summary, duration_minutes=duration))

        route = TransitRoute(
            destination=destination,
            duration_minutes=journey.get("duration", 0),
            departure_time=journey.get("startDateTime", ""),
            arrival_time=journey.get("arrivalDateTime", ""),
            legs=legs,
        )
        log.debug(
            "TfL: %s → %d min (%d legs)",
            destination.name, route.duration_minutes, len(legs),
        )
        _TRANSIT_CACHE.set(cache_key, route)
        _TRANSIT_DISK_CACHE.set(cache_key, route.model_dump())
        return route

    def fetch_batch(
        self,
        origin: Location,
        destinations: list[WaterBody],
        date: str,
        time: str,
    ) -> list[TransitRoute]:
        """Find transit routes to multiple destinations, skipping failures."""
        log.debug("Finding transit routes to %d destinations", len(destinations))
        results: list[TransitRoute] = []
        if not destinations:
            return results

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(destinations))) as pool:
            futures = [
                pool.submit(self.fetch, origin=origin, destination=dest, date=date, time=time)
                for dest in destinations
            ]
            for future in as_completed(futures):
                route = future.result()
                if route is not None:
                    results.append(route)
        results.sort(key=lambda r: r.duration_minutes)
        log.info("Transit: %d/%d destinations reachable", len(results), len(destinations))
        return results


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s  %(levelname)-8s  %(message)s")

    parser = argparse.ArgumentParser(description="Find transit route to a location")
    parser.add_argument("--origin-lat", type=float, required=True)
    parser.add_argument("--origin-lon", type=float, required=True)
    parser.add_argument("--dest-lat", type=float, required=True)
    parser.add_argument("--dest-lon", type=float, required=True)
    parser.add_argument("--dest-name", type=str, default="Destination")
    parser.add_argument("--date", type=str, required=True, help="YYYY-MM-DD")
    parser.add_argument("--time", type=str, required=True, help="HH:MM")
    args = parser.parse_args()

    source = TransitSource()
    origin = Location(lat=args.origin_lat, lon=args.origin_lon)
    dest = WaterBody(name=args.dest_name, type="unknown", lat=args.dest_lat, lon=args.dest_lon)
    route = source.fetch(origin=origin, destination=dest, date=args.date, time=args.time)
    if route:
        print(f"  {route.destination.name}: {route.duration_minutes} min")
        print(f"  Depart: {route.departure_time}")
        print(f"  Arrive: {route.arrival_time}")
        for leg in route.legs:
            print(f"    {leg.mode}: {leg.summary} ({leg.duration_minutes} min)")
    else:
        print("  No route found")
