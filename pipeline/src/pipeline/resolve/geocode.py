"""US Census Bureau Geocoder client.

Free, no API key, no documented rate limit (checked their FAQ directly) but
also no SLA — we self-impose pacing rather than treat "undocumented" as
"unlimited". For this anchor-scoped resolver run the candidate volume needing
geocoding is small (tens of records, not thousands), so the single-address
endpoint is used directly; the full-universe run should switch to the batch
endpoint (up to 10,000 addresses/file in one call) instead of looping
single-address calls thousands of times.
"""

import time

import httpx

ONELINE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
REQUEST_PACING_SECONDS = 0.5
MAX_RETRIES = 3


def geocode_oneline(address_line: str, city: str, state: str, zip_code: str) -> tuple[float, float] | None:
    query = ", ".join(p for p in [address_line, city, state, zip_code] if p)
    if not query.strip():
        return None

    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(REQUEST_PACING_SECONDS)
            resp = httpx.get(
                ONELINE_URL,
                params={"address": query, "benchmark": "Public_AR_Current", "format": "json"},
                timeout=30.0,
            )
            resp.raise_for_status()
            matches = resp.json().get("result", {}).get("addressMatches", [])
            if not matches:
                return None
            coords = matches[0]["coordinates"]
            return coords["y"], coords["x"]  # (lat, lon)
        except (httpx.HTTPStatusError, httpx.RequestError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(2**attempt)
    return None
