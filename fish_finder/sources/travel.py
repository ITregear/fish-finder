from __future__ import annotations

import logging

import httpx

from ..models import Location, TravelInfo, WaterBody
from .base import DataSource

log = logging.getLogger(__name__)

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


class TravelSource(DataSource):
    """Calculates driving times via the OSRM public routing API."""

    def fetch(self, *, origin: Location, destination: WaterBody) -> TravelInfo | None:
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
            return info
        except (httpx.HTTPError, KeyError, IndexError) as e:
            log.warning("OSRM failed for %s: %s", destination.name, e)
            return None

    def fetch_batch(
        self,
        origin: Location,
        destinations: list[WaterBody],
    ) -> list[TravelInfo]:
        """Fetch travel info for multiple destinations, skipping failures."""
        log.debug("Calculating travel to %d destinations", len(destinations))
        results: list[TravelInfo] = []
        for dest in destinations:
            info = self.fetch(origin=origin, destination=dest)
            if info is not None:
                results.append(info)
        results.sort(key=lambda t: t.duration_minutes)
        log.info("Travel: %d/%d destinations reachable", len(results), len(destinations))
        return results


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
