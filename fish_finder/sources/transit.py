from __future__ import annotations

import logging

import httpx

from ..models import Location, TransitLeg, TransitRoute, WaterBody
from .base import DataSource

log = logging.getLogger(__name__)

TFL_JOURNEY_URL = "https://api.tfl.gov.uk/Journey/JourneyResults"


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
            return None

        journeys = data.get("journeys", [])
        if not journeys:
            log.debug("TfL: no route to %s", destination.name)
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
        for dest in destinations:
            route = self.fetch(origin=origin, destination=dest, date=date, time=time)
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
