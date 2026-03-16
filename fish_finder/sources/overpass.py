"""Shared Overpass API client with retry logic and multiple endpoints."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

MAX_RETRIES = 2
RETRY_DELAY = 2.0


def query(overpass_ql: str) -> list[dict]:
    """Execute an Overpass QL query, trying multiple endpoints with retries."""
    last_error: Exception | None = None

    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                log.debug(
                    "Overpass request to %s (attempt %d/%d)",
                    endpoint, attempt, MAX_RETRIES,
                )
                resp = httpx.post(
                    endpoint,
                    data={"data": overpass_ql},
                    timeout=30.0,
                )
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                log.debug("Overpass returned %d elements", len(elements))
                return elements

            except httpx.TimeoutException as e:
                log.warning(
                    "Overpass timeout on %s (attempt %d/%d)",
                    endpoint, attempt, MAX_RETRIES,
                )
                last_error = e

            except httpx.HTTPStatusError as e:
                log.warning(
                    "Overpass HTTP %d from %s (attempt %d/%d): %s",
                    e.response.status_code, endpoint, attempt,
                    MAX_RETRIES, e.response.text[:200],
                )
                last_error = e
                if e.response.status_code in {429, 503, 504}:
                    time.sleep(RETRY_DELAY * attempt)
                    continue
                break

            except httpx.HTTPError as e:
                log.warning(
                    "Overpass connection error on %s (attempt %d/%d): %s",
                    endpoint, attempt, MAX_RETRIES, e,
                )
                last_error = e

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

        log.info("All retries exhausted for %s, trying next endpoint", endpoint)

    raise ConnectionError(
        f"All Overpass endpoints failed: {last_error}"
    ) from last_error
