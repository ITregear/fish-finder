from __future__ import annotations

import logging

from ..models import Location, WaterBody
from ..utils import haversine_km
from . import overpass
from .base import DataSource

log = logging.getLogger(__name__)


class WatersSource(DataSource):
    """Finds water bodies and fisheries near a location via the Overpass (OSM) API."""

    def fetch(self, *, location: Location, radius_m: int = 30_000) -> list[WaterBody]:
        ql = _build_query(location, radius_m)
        elements = overpass.query(ql)

        waters: list[WaterBody] = []
        seen: set[str] = set()

        for el in elements:
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lon = el.get("lon") or el.get("center", {}).get("lon")
            if not lat or not lon:
                continue

            tags = el.get("tags", {})
            name = tags.get("name", "")
            if not name or name in seen:
                continue
            seen.add(name)

            water_type = (
                tags.get("water")
                or tags.get("natural")
                or tags.get("leisure")
                or "unknown"
            )

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

        waters.sort(key=lambda w: w.distance_km)
        log.info(
            "Found %d water bodies within %dm of (%.4f, %.4f)",
            len(waters), radius_m, location.lat, location.lon,
        )
        return waters


def _build_query(loc: Location, radius_m: int) -> str:
    r, lat, lon = radius_m, loc.lat, loc.lon
    return f"""
[out:json][timeout:25];
(
  node["natural"="water"](around:{r},{lat},{lon});
  way["natural"="water"](around:{r},{lat},{lon});
  node["leisure"="fishing"](around:{r},{lat},{lon});
  way["leisure"="fishing"](around:{r},{lat},{lon});
  node["water"](around:{r},{lat},{lon});
  way["water"](around:{r},{lat},{lon});
);
out center 50;
"""


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
