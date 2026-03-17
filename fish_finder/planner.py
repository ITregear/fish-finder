from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .llm.client import LLMClient
from .llm.prompts import PARSE_QUERY_SYSTEM, RECOMMEND_SYSTEM, RECOMMEND_USER
from .models import (
    FishingIntent,
    ParkingSpot,
    Profile,
    SessionRecommendation,
    TravelInfo,
    TransitRoute,
    WeatherForecast,
)
from .sources.parking import ParkingSource
from .sources.transit import TransitSource
from .sources.travel import TravelSource
from .sources.waters import WatersSource
from .sources.weather import WeatherSource
from .utils import extract_json

log = logging.getLogger(__name__)

MAX_CANDIDATE_WATERS = 15


class Planner:
    """Orchestrates data gathering and LLM calls to produce a session plan."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.llm = LLMClient()
        self.weather_src = WeatherSource()
        self.waters_src = WatersSource()
        self.travel_src = TravelSource()
        self.parking_src = ParkingSource()
        self.transit_src = TransitSource()
        log.debug(
            "Planner initialised for %s (%.4f, %.4f)",
            profile.location.address, profile.location.lat, profile.location.lon,
        )

    def parse_query(self, query: str) -> FishingIntent:
        """Use the LLM to convert a natural language query into structured intent."""
        log.info("Parsing query: %s", query)
        p = self.profile
        system = PARSE_QUERY_SYSTEM.format(
            now=datetime.now().strftime("%A %Y-%m-%d %H:%M"),
            address=p.location.address,
            lat=p.location.lat,
            lon=p.location.lon,
            species=", ".join(p.target_species) or "not specified",
            methods=", ".join(p.methods) or "not specified",
            max_travel=p.max_travel_minutes,
            work_end=p.work_end,
        )
        raw = self.llm.complete(system, query)
        data = extract_json(raw)
        intent = FishingIntent(**data)
        log.info(
            "Intent: %s %s, %d min, species=%s, mode=%s",
            intent.date, intent.start_time, intent.duration_minutes,
            intent.species_preference, intent.travel_mode,
        )
        return intent

    def get_weather(self, intent: FishingIntent | None = None) -> WeatherForecast:
        forecast_days = 3
        if intent:
            from datetime import date
            try:
                target = date.fromisoformat(intent.date)
                days_ahead = (target - date.today()).days + 1
                forecast_days = max(3, min(days_ahead, 16))
            except ValueError:
                pass
        log.debug("Fetching %d-day forecast (session date: %s)", forecast_days, intent.date if intent else "unknown")
        return self.weather_src.fetch(location=self.profile.location, forecast_days=forecast_days)

    def find_waters(self) -> list:
        radius = _travel_to_radius(self.profile.max_travel_minutes)
        log.debug("Search radius: %dm (from %d min travel)", radius, self.profile.max_travel_minutes)
        waters = self.waters_src.fetch(
            location=self.profile.location,
            radius_m=radius,
            permits=self.profile.permits,
        )
        return waters[:MAX_CANDIDATE_WATERS]

    def gather_base_context(self, intent: FishingIntent) -> tuple[WeatherForecast | None, list]:
        """Fetch weather and candidate waters concurrently for lower latency."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            weather_future = pool.submit(self.get_weather, intent)
            waters_future = pool.submit(self.find_waters)

            weather: WeatherForecast | None
            try:
                weather = weather_future.result()
            except Exception:
                log.exception("Weather fetch failed")
                weather = None

            waters = waters_future.result()
            return weather, waters

    def get_drive_times(self, waters) -> list[TravelInfo]:
        """Calculate driving times via OSRM (static — not time-dependent)."""
        log.debug("OSRM provides static travel times (no traffic modelling)")
        infos = self.travel_src.fetch_batch(self.profile.location, waters)
        filtered = [
            t for t in infos
            if t.duration_minutes <= self.profile.max_travel_minutes
        ]
        log.info(
            "Drive filter: %d/%d within %d min",
            len(filtered), len(infos), self.profile.max_travel_minutes,
        )
        return filtered

    def get_transit_routes(self, waters, intent: FishingIntent) -> list[TransitRoute]:
        """Find public transport routes via TfL (uses specific departure time)."""
        routes = self.transit_src.fetch_batch(
            self.profile.location,
            waters,
            date=intent.date,
            time=intent.start_time,
        )
        filtered = [
            r for r in routes
            if r.duration_minutes <= self.profile.max_travel_minutes
        ]
        log.info(
            "Transit filter: %d/%d within %d min",
            len(filtered), len(routes), self.profile.max_travel_minutes,
        )
        return filtered

    def find_parking(self, travel_infos: list[TravelInfo]) -> dict[str, list[ParkingSpot]]:
        waters = [t.destination for t in travel_infos]
        return self.parking_src.fetch_for_waters(waters)

    def recommend(
        self,
        intent: FishingIntent,
        weather: WeatherForecast | None,
        travel_data: list[TravelInfo] | list[TransitRoute],
        parking: dict[str, list[ParkingSpot]] | None = None,
    ) -> SessionRecommendation:
        """Synthesise all gathered data into a single session recommendation."""
        log.info("Generating recommendation from %d candidate locations", len(travel_data))
        relevant_weather = _filter_weather(weather, intent) if weather else []

        permits_text = ""
        if self.profile.permits:
            permits_text = "\nPermits held:\n" + "\n".join(
                f"  - {p.name}: {p.covers}" for p in self.profile.permits
            )

        profile_summary = (
            f"Location: {self.profile.location.address}\n"
            f"Preferred species: {', '.join(self.profile.target_species)}\n"
            f"Preferred methods: {', '.join(self.profile.methods)}\n"
            f"Max travel: {self.profile.max_travel_minutes} min\n"
            f"Travel mode: {intent.travel_mode}"
            f"{permits_text}"
        )

        if intent.travel_mode == "train":
            locations_text = _format_transit_locations(travel_data)
            extra_context = _format_transit_details(travel_data)
        else:
            locations_text = _format_driving_locations(travel_data)
            extra_context = _format_parking_context(parking) if parking else ""

        weather_text = "\n".join(
            f"- {h.time}: {h.temperature_c}°C, "
            f"precip {h.precipitation_mm}mm, "
            f"wind {h.wind_speed_kmh}km/h {_wind_dir(h.wind_direction)}, "
            f"cloud {h.cloud_cover_pct}%"
            for h in relevant_weather
        ) or "Weather data unavailable."

        user_msg = RECOMMEND_USER.format(
            profile=profile_summary,
            intent=intent.model_dump_json(indent=2),
            weather=weather_text,
            locations=locations_text,
            extra_context=extra_context,
        )

        raw = self.llm.complete(RECOMMEND_SYSTEM, user_msg)
        data = extract_json(raw)
        rec = SessionRecommendation(**data)
        log.info("Recommended: %s (%s)", rec.location_name, rec.location_type)
        return rec


def _format_driving_locations(infos: list[TravelInfo]) -> str:
    if not infos:
        return "No locations found within travel range."
    return "\n".join(
        f"- {t.destination.name} ({t.destination.type}, "
        f"access: {t.destination.access}) — "
        f"{t.duration_minutes} min drive, {t.distance_km} km"
        for t in infos
    )


def _format_transit_locations(routes: list[TransitRoute]) -> str:
    if not routes:
        return "No locations reachable by public transport."
    return "\n".join(
        f"- {r.destination.name} ({r.destination.type}, "
        f"access: {r.destination.access}) — "
        f"{r.duration_minutes} min by transit"
        for r in routes
    )


def _format_transit_details(routes: list[TransitRoute]) -> str:
    if not routes:
        return ""
    lines = ["Transit route details:"]
    for r in routes:
        leg_summary = " → ".join(
            f"{leg.summary} ({leg.duration_minutes} min)"
            for leg in r.legs
        )
        lines.append(
            f"- {r.destination.name}: depart {r.departure_time}, "
            f"arrive {r.arrival_time}\n  {leg_summary}"
        )
    return "\n".join(lines)


def _format_parking_context(parking: dict[str, list]) -> str:
    if not parking:
        return ""
    lines = ["Nearby parking:"]
    for water_name, spots in parking.items():
        spot_descs = []
        for s in spots:
            fee_str = f", {s.fee}" if s.fee != "unknown" else ""
            spot_descs.append(f"{s.name} ({s.distance_m}m{fee_str})")
        lines.append(f"- {water_name}: {' | '.join(spot_descs)}")
    return "\n".join(lines)


def _wind_dir(degrees: int) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(degrees / 45) % 8]


def _travel_to_radius(max_minutes: int) -> int:
    """Rough conversion: 1 min driving ~ 800m radius (accounts for road indirection)."""
    return max(10_000, max_minutes * 800)


def _filter_weather(
    forecast: WeatherForecast,
    intent: FishingIntent,
) -> list:
    """Return only the hourly slots relevant to the session window."""
    try:
        start_h = int(intent.start_time.split(":")[0])
    except (ValueError, IndexError):
        start_h = 17
    end_h = start_h + (intent.duration_minutes // 60) + 1

    return [
        h for h in forecast.hours
        if h.time.startswith(intent.date) and _hour_in_range(h.time, start_h, end_h)
    ]


def _hour_in_range(iso_time: str, start_h: int, end_h: int) -> bool:
    try:
        hour = int(iso_time.split("T")[1].split(":")[0])
        return start_h - 1 <= hour <= end_h
    except (ValueError, IndexError):
        return False
