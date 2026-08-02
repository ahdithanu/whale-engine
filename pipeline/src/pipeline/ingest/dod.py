"""USASpending prime contract award ingestion, filtered to our target NAICS set.

Source: USASpending API v2, POST https://api.usaspending.gov/api/v2/search/spending_by_award/
— public, unauthenticated JSON API. Endpoint, filters, and response shape were
verified against a live call, not just the docs (the docs' listed "NAICS Code"
response field came back null in practice, which is why we query per-NAICS-code
and tag each batch ourselves instead of trusting that field).

We loop one query per NAICS code rather than a single `naics_codes.require`
list with all 22 codes, so every award batch can be tagged with the exact code
that matched, and so no single query risks the pagination cap.

Prime contracts only — award_type_codes A/B/C/D (BPA call, purchase order,
delivery order, definitive contract). This excludes IDVs (indefinite delivery
vehicles, which are contract frameworks, not the individual orders placed
under them) and subawards (subawards=False).

Place of performance is what matters here (which plant the work happens at),
not recipient address — USASpending place-of-performance data is typically
city/county/zip granularity, not street address. That's a real limitation to
carry into entity resolution, not a bug in this ingestion.

The search endpoint 500s on deep pagination — in practice this hit at exactly
page 101 (offset 10,000), which is the classic Elasticsearch default
max_result_window. High-volume codes like 336120 (heavy duty truck
manufacturing — Oshkosh's own code) blow past that in a flat 24-month query.

Two earlier attempts at this both left silent data loss:

1. Unconditionally slicing every code into 24 monthly queries multiplied
   request volume ~25x for codes that never needed it, and that volume
   triggered sustained connection resets partway through a run.
2. Splitting only when a fetch loop hit MAX_PAGES_PER_WINDOW with
   `hasNext: true` seemed right, but isn't: cross-checked against the
   separate spending_by_award_count endpoint, several codes returned
   `hasNext: false` and stopped **long before** the true total — NAICS 336413
   ("other aircraft parts") has 120,572 real awards; the search endpoint
   handed back 9,939 and claimed it was done. `hasNext` cannot be trusted
   near this boundary, so it cannot be the thing that decides whether to
   split.

We now ask the *count* endpoint for the true total before ever fetching a
window, and split (by date, recursively) whenever that true count exceeds
SPLIT_THRESHOLD, regardless of what the search endpoint's hasNext later
claims. After fetching, we still compare the number of awards actually
returned against the count endpoint's total and warn loudly on any mismatch —
belt and suspenders, because we've now seen this API under-report twice in
two different ways. An award modified across a split boundary can appear in
both halves, so results are deduplicated by Award ID before being written out.
"""

import json
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import httpx
import polars as pl

from pipeline.config import DATA_DIR, DOD_DB_PATH, RAW_DIR
from pipeline.universe import load_universe

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
COUNT_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award_count/"
PRIME_CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]
PAGE_LIMIT = 100
MAX_PAGES_PER_WINDOW = 100  # 10k awards per window — the confirmed deep-pagination cap
SPLIT_THRESHOLD = 5_000  # split proactively at half the observed cap, for margin
MAX_SPLIT_DEPTH = 12  # halving a 730-day window 12x bottoms out at ~4 hours; plenty
MAX_RETRIES = 5
REQUEST_PACING_SECONDS = 0.5  # the API disconnects under bursty unpaced traffic; be a good citizen

RAW_DOD_DIR = RAW_DIR / "dod"

FIELDS = [
    "Recipient Name",
    "Recipient Location",
    "Primary Place of Performance",
    "Award Amount",
    "Awarding Agency",
    "Award ID",
    "Start Date",
    "End Date",
]


def _trailing_24_months() -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=730)
    return start.isoformat(), end.isoformat()


def _post_with_retry(client: httpx.Client, url: str, body: dict) -> dict:
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(REQUEST_PACING_SECONDS)
            resp = client.post(url, json=body, timeout=60.0)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                backoff = min(2**attempt, 20)
                print(f"[ingest.dod] request failed ({exc!r}), retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(backoff)
    raise last_exc


def _filters(naics_code: str, start_date: str, end_date: str) -> dict:
    return {
        "award_type_codes": PRIME_CONTRACT_TYPE_CODES,
        "time_period": [{"start_date": start_date, "end_date": end_date}],
        "naics_codes": {"require": [naics_code]},
        "place_of_performance_scope": "domestic",
    }


def _get_true_count(client: httpx.Client, naics_code: str, start_date: str, end_date: str) -> int:
    data = _post_with_retry(client, COUNT_URL, {"filters": _filters(naics_code, start_date, end_date)})
    return data.get("results", {}).get("contracts", 0)


def _fetch_window_pages(client: httpx.Client, naics_code: str, start_date: str, end_date: str) -> list[dict]:
    awards: list[dict] = []
    page = 1
    while page <= MAX_PAGES_PER_WINDOW:
        body = {
            "filters": _filters(naics_code, start_date, end_date),
            "fields": FIELDS,
            "page": page,
            "limit": PAGE_LIMIT,
            "subawards": False,
            "sort": "Award Amount",
            "order": "desc",
        }
        data = _post_with_retry(client, SEARCH_URL, body)
        results = data.get("results", [])
        for r in results:
            r["_naics_code"] = naics_code
        awards.extend(results)
        if not data.get("page_metadata", {}).get("hasNext"):
            break
        page += 1
    return awards


def _fetch_window(client: httpx.Client, naics_code: str, start_date: str, end_date: str, depth: int = 0) -> dict[str, dict]:
    """Fetch every award for [start_date, end_date], splitting proactively on
    the *true* count from the count endpoint — not on the search endpoint's
    self-reported hasNext, which is unreliable near the pagination boundary
    (see module docstring)."""
    true_count = _get_true_count(client, naics_code, start_date, end_date)
    if true_count == 0:
        return {}

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    can_split = start < end and depth < MAX_SPLIT_DEPTH

    if true_count > SPLIT_THRESHOLD and can_split:
        mid = start + (end - start) // 2
        print(f"[ingest.dod] NAICS {naics_code} {start_date}..{end_date}: {true_count} true awards > {SPLIT_THRESHOLD}, splitting at {mid.isoformat()}")
        left = _fetch_window(client, naics_code, start_date, mid.isoformat(), depth + 1)
        right = _fetch_window(client, naics_code, (mid + timedelta(days=1)).isoformat(), end_date, depth + 1)
        merged = dict(left)
        merged.update(right)
        return merged

    if true_count > SPLIT_THRESHOLD:
        print(f"[ingest.dod] WARNING: NAICS {naics_code} {start_date}..{end_date} has {true_count} true awards and can't be split further (depth {depth}); fetching best-effort, likely incomplete")

    awards = _fetch_window_pages(client, naics_code, start_date, end_date)
    by_award_id = {a.get("Award ID"): a for a in awards}
    if len(by_award_id) < true_count:
        print(f"[ingest.dod] WARNING: NAICS {naics_code} {start_date}..{end_date}: fetched {len(by_award_id)} awards but count endpoint says {true_count} — some results missing despite hasNext=false")
    return by_award_id


def _fetch_naics_awards(client: httpx.Client, naics_code: str, start_date: str, end_date: str) -> list[dict]:
    return list(_fetch_window(client, naics_code, start_date, end_date).values())


def _cache_path(naics_code: str, start_date: str, end_date: str) -> Path:
    return RAW_DOD_DIR / f"{naics_code}_{start_date}_{end_date}.json"


def _fetch_naics_awards_cached(naics_code: str, start_date: str, end_date: str) -> list[dict]:
    """Per-NAICS-code result cache, keyed on the query window.

    The search endpoint is flaky enough (deep-pagination 500s, sustained
    connection resets under request volume) that losing all prior progress to
    one bad code partway through a 22-code run is a real cost, not a
    hypothetical one — it happened on the first two real runs. Caching each
    code's result to disk as soon as it succeeds means a re-run only has to
    redo the codes that actually failed, matching the project's "never redo
    work because a downstream step failed" idempotency principle for the
    bulk-file sources.

    A fresh httpx.Client per NAICS code, not one shared client for all 22 —
    the failures we saw were not correlated with query volume alone (isolated
    single-request probes against codes that had just failed in-loop
    succeeded instantly), so a long-lived connection reused across hundreds of
    requests is a plausible contributor and costs nothing to rule out.
    """
    cache_file = _cache_path(naics_code, start_date, end_date)
    if cache_file.exists():
        print(f"[ingest.dod] NAICS {naics_code}: cached, skipping fetch")
        return json.loads(cache_file.read_text())

    with httpx.Client() as client:
        awards = _fetch_naics_awards(client, naics_code, start_date, end_date)
    RAW_DOD_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(awards))
    return awards


def _row(a: dict) -> dict:
    recipient_loc = a.get("Recipient Location") or {}
    pop = a.get("Primary Place of Performance") or {}
    return {
        "award_id": a.get("Award ID"),
        "naics_code": a.get("_naics_code"),
        "recipient_name": a.get("Recipient Name"),
        "recipient_address_line1": recipient_loc.get("address_line1"),
        "recipient_city": recipient_loc.get("city_name"),
        "recipient_state": recipient_loc.get("state_code"),
        "recipient_zip": recipient_loc.get("zip5"),
        "pop_city": pop.get("city_name"),
        "pop_county": pop.get("county_name"),
        "pop_state": pop.get("state_code"),
        "pop_zip": pop.get("zip5"),
        "award_amount": a.get("Award Amount"),
        "awarding_agency": a.get("Awarding Agency"),
        "award_date": a.get("Start Date"),
        "period_of_performance_end": a.get("End Date"),
    }


def build_dod_awards(con: duckdb.DuckDBPyConnection) -> int:
    universe = load_universe()
    naics_codes = sorted({code for v in universe.verticals.values() for code in v.codes})
    start_date, end_date = _trailing_24_months()

    all_awards: list[dict] = []
    failed_codes: list[str] = []
    for naics_code in naics_codes:
        try:
            batch = _fetch_naics_awards_cached(naics_code, start_date, end_date)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            print(f"[ingest.dod] FAILED NAICS {naics_code} after {MAX_RETRIES} retries: {exc!r} — skipping, re-run to retry just this code")
            failed_codes.append(naics_code)
            continue
        print(f"[ingest.dod] NAICS {naics_code}: {len(batch)} prime awards")
        all_awards.extend(batch)

    if failed_codes:
        print(f"[ingest.dod] WARNING: {len(failed_codes)}/{len(naics_codes)} NAICS codes failed and are NOT in dod_awards: {failed_codes}")

    rows = [_row(a) for a in all_awards]
    df = pl.DataFrame(
        rows,
        schema={
            "award_id": pl.Utf8, "naics_code": pl.Utf8, "recipient_name": pl.Utf8,
            "recipient_address_line1": pl.Utf8, "recipient_city": pl.Utf8,
            "recipient_state": pl.Utf8, "recipient_zip": pl.Utf8,
            "pop_city": pl.Utf8, "pop_county": pl.Utf8, "pop_state": pl.Utf8, "pop_zip": pl.Utf8,
            "award_amount": pl.Float64, "awarding_agency": pl.Utf8,
            "award_date": pl.Utf8, "period_of_performance_end": pl.Utf8,
        },
    ) if rows else pl.DataFrame(schema={
        "award_id": pl.Utf8, "naics_code": pl.Utf8, "recipient_name": pl.Utf8,
        "recipient_address_line1": pl.Utf8, "recipient_city": pl.Utf8,
        "recipient_state": pl.Utf8, "recipient_zip": pl.Utf8,
        "pop_city": pl.Utf8, "pop_county": pl.Utf8, "pop_state": pl.Utf8, "pop_zip": pl.Utf8,
        "award_amount": pl.Float64, "awarding_agency": pl.Utf8,
        "award_date": pl.Utf8, "period_of_performance_end": pl.Utf8,
    })

    con.register("dod_out", df)
    con.execute("CREATE OR REPLACE TABLE dod_awards AS SELECT * FROM dod_out")
    con.unregister("dod_out")
    return df.height


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DOD_DB_PATH))
    try:
        n = build_dod_awards(con)
        print(f"[ingest.dod] dod_awards: {n:,} prime contract awards, trailing 24 months")
    finally:
        con.close()


if __name__ == "__main__":
    main()
