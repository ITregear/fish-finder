from __future__ import annotations

import logging

import httpx

from ..models import HourlyWeather, Location, WeatherForecast
from .base import DataSource

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_PARAMS = ",".join([
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "cloud_cover",
])


class WeatherSource(DataSource):
    """Fetches weather forecasts from the Open-Meteo API (free, no key)."""

    def fetch(self, *, location: Location, forecast_days: int = 3) -> WeatherForecast:
        log.debug(
            "Fetching weather for (%.4f, %.4f), %d days",
            location.lat, location.lon, forecast_days,
        )
        resp = httpx.get(
            OPEN_METEO_URL,
            params={
                "latitude": location.lat,
                "longitude": location.lon,
                "hourly": HOURLY_PARAMS,
                "forecast_days": forecast_days,
                "timezone": "auto",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        hourly = data["hourly"]

        hours = [
            HourlyWeather(
                time=hourly["time"][i],
                temperature_c=hourly["temperature_2m"][i],
                precipitation_mm=hourly["precipitation"][i],
                wind_speed_kmh=hourly["wind_speed_10m"][i],
                wind_direction=hourly["wind_direction_10m"][i],
                cloud_cover_pct=hourly["cloud_cover"][i],
            )
            for i in range(len(hourly["time"]))
        ]

        log.info("Weather: %d hourly slots fetched", len(hours))
        return WeatherForecast(location=location, hours=hours)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch weather forecast for a location")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s  %(levelname)-8s  %(message)s")

    source = WeatherSource()
    forecast = source.fetch(
        location=Location(lat=args.lat, lon=args.lon),
        forecast_days=args.days,
    )
    for h in forecast.hours[:12]:
        print(
            f"  {h.time}  {h.temperature_c:5.1f}°C  "
            f"rain {h.precipitation_mm:.1f}mm  "
            f"wind {h.wind_speed_kmh:.0f}km/h  "
            f"cloud {h.cloud_cover_pct}%"
        )
    print(f"\n  Total: {len(forecast.hours)} hours (showing first 12)")
