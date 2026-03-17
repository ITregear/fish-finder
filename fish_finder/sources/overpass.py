"""Shared Overpass API client with retry logic and multiple endpoints."""

from __future__ import annotations

import logging
import time

import httpx

from ..cache import TTLCache
from ..disk_cache import PersistentTTLCache

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

MAX_RETRIES = 3
RETRY_DELAY = 3.0

_OVERPASS_CACHE = TTLCache[str, list[dict]](ttl_seconds=10 * 60, max_entries=128)
_OVERPASS_DISK_CACHE = PersistentTTLCache[list[dict]]("overpass_elements", ttl_seconds=6 * 60 * 60, max_entries=256)


def query(
    overpass_ql: str,
    *,
    max_retries: int = MAX_RETRIES,
    request_timeout: float = 30.0,
    retry_delay: float = RETRY_DELAY,
) -> list[dict]:
    """Execute an Overpass QL query, trying multiple endpoints with retries."""
    cached = _OVERPASS_CACHE.get(overpass_ql)
    if cached is not None:
        log.debug("Overpass cache hit (%d elements)", len(cached))
        return cached
    disk_cached = _OVERPASS_DISK_CACHE.get(overpass_ql)
    if disk_cached is not None:
        _OVERPASS_CACHE.set(overpass_ql, disk_cached)
        log.debug("Overpass disk cache hit (%d elements)", len(disk_cached))
        return disk_cached

    last_error: Exception | None = None

    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(1, max_retries + 1):
            try:
                log.debug(
                    "Overpass request to %s (attempt %d/%d)",
                    endpoint, attempt, max_retries,
                )
                resp = httpx.post(
                    endpoint,
                    data={"data": overpass_ql},
                    timeout=request_timeout,
                )
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                log.debug("Overpass returned %d elements", len(elements))
                _OVERPASS_CACHE.set(overpass_ql, elements)
                _OVERPASS_DISK_CACHE.set(overpass_ql, elements)
                return elements

            except httpx.TimeoutException as e:
                log.warning(
                    "Overpass timeout on %s (attempt %d/%d)",
                    endpoint, attempt, max_retries,
                )
                last_error = e

            except httpx.HTTPStatusError as e:
                log.warning(
                    "Overpass HTTP %d from %s (attempt %d/%d): %s",
                    e.response.status_code, endpoint, attempt,
                    max_retries, e.response.text[:200],
                )
                last_error = e
                if e.response.status_code in {429, 503, 504}:
                    time.sleep(retry_delay * attempt)
                    continue
                break

            except httpx.HTTPError as e:
                log.warning(
                    "Overpass connection error on %s (attempt %d/%d): %s",
                    endpoint, attempt, max_retries, e,
                )
                last_error = e

            if attempt < max_retries:
                time.sleep(retry_delay * attempt)

        log.info("All retries exhausted for %s, trying next endpoint", endpoint)

    raise ConnectionError(
        f"All Overpass endpoints failed: {last_error}"
    ) from last_error
