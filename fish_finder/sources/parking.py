from __future__ import annotations

import logging

from ..models import Location, ParkingSpot, WaterBody
from ..utils import haversine_km
from . import overpass
from .base import DataSource

log = logging.getLogger(__name__)


class ParkingSource(DataSource):
    """Finds parking spots near water bodies via OSM/Overpass."""

    def fetch(self, *, location: Location, radius_m: int = 2000) -> list[ParkingSpot]:
        ql = _build_query(location, radius_m)
        try:
            elements = overpass.query(ql, max_retries=1, request_timeout=8.0, retry_delay=1.0)
        except ConnectionError:
            log.warning("Parking search failed — Overpass unavailable")
            return []

        return _parse_spots(elements, location)

    def fetch_for_waters(self, waters: list[WaterBody]) -> dict[str, list[ParkingSpot]]:
        """Find parking for multiple water bodies in a single Overpass query."""
        if not waters:
            return {}

        ql = _build_batch_query(waters, radius_m=2000)
        try:
            elements = overpass.query(ql, max_retries=1, request_timeout=8.0, retry_delay=1.0)
        except ConnectionError:
            log.warning("Parking batch search failed — Overpass unavailable")
            return {}

        all_spots = _parse_spots(elements, Location(lat=0, lon=0))

        results: dict[str, list[ParkingSpot]] = {}
        for water in waters:
            nearby: list[ParkingSpot] = []
            for spot in all_spots:
                dist = haversine_km(water.lat, water.lon, spot.lat, spot.lon) * 1000
                if dist <= 2000:
                    nearby.append(spot.model_copy(update={"distance_m": round(dist)}))
            nearby.sort(key=lambda s: (s.fee != "free", s.distance_m))
            if nearby:
                results[water.name] = nearby[:3]

        log.info("Parking: found spots for %d/%d waters", len(results), len(waters))
        return results


def _parse_spots(elements: list[dict], ref_location: Location) -> list[ParkingSpot]:
    spots: list[ParkingSpot] = []
    seen: set[str] = set()

    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if not lat or not lon:
            continue

        key = f"{round(lat, 4)},{round(lon, 4)}"
        if key in seen:
            continue
        seen.add(key)

        tags = el.get("tags", {})
        name = tags.get("name", "Unnamed car park")

        fee_tag = tags.get("fee", "").lower()
        if fee_tag in {"no", "free"}:
            fee = "free"
        elif fee_tag in {"yes"}:
            fee = "paid"
        else:
            fee = "unknown"

        dist = 0
        if ref_location.lat != 0:
            dist = round(haversine_km(ref_location.lat, ref_location.lon, lat, lon) * 1000)

        spots.append(ParkingSpot(name=name, lat=lat, lon=lon, distance_m=dist, fee=fee))

    spots.sort(key=lambda s: (s.fee != "free", s.distance_m))
    return spots


def _build_query(loc: Location, radius_m: int) -> str:
    return f"""
[out:json][timeout:15];
(
  node["amenity"="parking"](around:{radius_m},{loc.lat},{loc.lon});
  way["amenity"="parking"](around:{radius_m},{loc.lat},{loc.lon});
);
out center 10;
"""


def _build_batch_query(waters: list[WaterBody], radius_m: int) -> str:
    unions = []
    for w in waters:
        unions.append(f'  node["amenity"="parking"](around:{radius_m},{w.lat},{w.lon});')
        unions.append(f'  way["amenity"="parking"](around:{radius_m},{w.lat},{w.lon});')
    union_str = "\n".join(unions)
    return f"""
[out:json][timeout:25];
(
{union_str}
);
out center 20;
"""


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s  %(levelname)-8s  %(message)s")

    parser = argparse.ArgumentParser(description="Find parking near a location")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius", type=int, default=2000)
    args = parser.parse_args()

    source = ParkingSource()
    spots = source.fetch(location=Location(lat=args.lat, lon=args.lon), radius_m=args.radius)
    for s in spots:
        print(f"  {s.name} — {s.distance_m}m ({s.fee})")
    print(f"\n  Total: {len(spots)} parking spots")
