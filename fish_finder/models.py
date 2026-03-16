from __future__ import annotations

from pydantic import BaseModel


class Location(BaseModel):
    address: str = ""
    lat: float
    lon: float


class Profile(BaseModel):
    location: Location
    target_species: list[str] = []
    methods: list[str] = []
    max_travel_minutes: int = 60
    work_end: str = "17:00"


class FishingIntent(BaseModel):
    """Structured representation of a user's natural language query."""

    date: str
    start_time: str
    duration_minutes: int
    species_preference: list[str] = []
    session_type: str = "quick"
    travel_mode: str = "car"
    notes: str = ""


class HourlyWeather(BaseModel):
    time: str
    temperature_c: float
    precipitation_mm: float
    wind_speed_kmh: float
    wind_direction: int = 0
    cloud_cover_pct: int = 0


class WeatherForecast(BaseModel):
    location: Location
    hours: list[HourlyWeather]


class WaterBody(BaseModel):
    name: str
    type: str
    lat: float
    lon: float
    distance_km: float = 0.0
    tags: dict[str, str] = {}


class TravelInfo(BaseModel):
    destination: WaterBody
    duration_minutes: float
    distance_km: float


class ParkingSpot(BaseModel):
    name: str
    lat: float
    lon: float
    distance_m: int = 0
    fee: str = "unknown"


class TransitLeg(BaseModel):
    mode: str
    summary: str
    duration_minutes: float


class TransitRoute(BaseModel):
    destination: WaterBody
    duration_minutes: float
    departure_time: str
    arrival_time: str
    legs: list[TransitLeg] = []


class TimelineEntry(BaseModel):
    time: str
    activity: str


class SessionRecommendation(BaseModel):
    """Final output from the planner — the recommended session."""

    location_name: str
    location_type: str
    travel_minutes: float
    target_species: list[str]
    weather_summary: str
    approach: str
    reasoning: str
    tackle: list[str] = []
    timeline: list[TimelineEntry] = []
    reminders: list[str] = []
    parking: str = ""
    transit_summary: str = ""
