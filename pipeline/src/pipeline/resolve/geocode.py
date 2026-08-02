"""US Census Bureau Geocoder client.

Free, no API key, no documented rate limit (checked their FAQ directly) but
also no SLA — we self-impose pacing rather than treat "undocumented" as
"unlimited". The anchor-scoped run used the single-address endpoint (tens of
records). Full-universe volume (thousands of OSHA establishments with no
lat/lon) uses the batch endpoint instead — confirmed live, not just from
docs: POST multipart to /geocoder/locations/addressbatch with an `addressFile`
CSV (unpaged, no header, columns id/street/city/state/zip) and a `benchmark`
field; response is CSV, no header, one row per input id:
    id, input_address, match_status, match_type, matched_address,
    "lon,lat", tiger_line_id, side
Up to 10,000 addresses per file — chunk larger inputs.
"""

import csv
import io
import time

import httpx

ONELINE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BATCH_CHUNK_SIZE = 10_000
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


def _batch_chunk(client: httpx.Client, rows: list[tuple]) -> dict[str, tuple[float, float]]:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    csv_bytes = buf.getvalue().encode("utf-8")

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.post(
                BATCH_URL,
                files={"addressFile": ("batch.csv", csv_bytes, "text/csv")},
                data={"benchmark": "Public_AR_Current"},
                timeout=300.0,
            )
            resp.raise_for_status()
            results: dict[str, tuple[float, float]] = {}
            for row in csv.reader(io.StringIO(resp.text)):
                if len(row) < 6 or row[2] != "Match":
                    continue
                lon_str, lat_str = row[5].split(",")
                results[row[0]] = (float(lat_str), float(lon_str))
            return results
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(2**attempt)
    print(f"[geocode] batch chunk of {len(rows)} failed after {MAX_RETRIES} retries: {last_exc!r}")
    return {}


def geocode_batch(records: list[dict]) -> None:
    """Mutates records in place, setting 'lat'/'lon' where the Census batch
    geocoder found a match. Each record needs 'source_id' (used as the batch
    row id — must be unique across the whole input), 'address_line', 'city',
    'state', 'zip'. Records with no address_line are skipped."""
    to_geocode = [r for r in records if r.get("lat") is None and r.get("address_line")]
    if not to_geocode:
        return
    print(f"[geocode] batch-geocoding {len(to_geocode)} records via Census addressbatch")

    by_id = {str(r["source_id"]): r for r in to_geocode}
    rows = [
        (str(r["source_id"]), r["address_line"] or "", r["city"] or "", r["state"] or "", r["zip"] or "")
        for r in to_geocode
    ]

    matched = 0
    with httpx.Client() as client:
        for start in range(0, len(rows), BATCH_CHUNK_SIZE):
            chunk = rows[start:start + BATCH_CHUNK_SIZE]
            results = _batch_chunk(client, chunk)
            for row_id, (lat, lon) in results.items():
                rec = by_id.get(row_id)
                if rec is not None:
                    rec["lat"], rec["lon"] = lat, lon
                    matched += 1
            time.sleep(REQUEST_PACING_SECONDS)
    print(f"[geocode] batch-geocoded {matched}/{len(to_geocode)} records")
