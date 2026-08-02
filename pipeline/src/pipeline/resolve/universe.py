"""Full-universe entity resolution — every EPA/OSHA/DoD record, not just the
three anchor accounts. Reuses the Stage 1 (facility) and Stage 2 (corporate)
logic proven in pipeline.resolve.anchors against Boeing/Oshkosh/Caterpillar;
what's different here is how accounts get formed in the first place.

The anchor run pre-filtered candidates with CANDIDATE_PATTERNS (hand-picked
company-identity substrings) before ever grouping by account. There's no
equivalent prefilter for "every company in the dataset" — so Stage 2 here
buckets records by normalized_company_name directly: an exact match after
stripping legal suffixes/punctuation IS the account, unless
corporate_map.yaml's alias table says otherwise. This is honestly a coarser
starting point than the anchor run: "CATERPILLAR INC" and "CATERPILLAR,
INC." collapse together automatically, but a subsidiary with a genuinely
different name (the next "Solar Turbines") won't group with its parent
unless it's already in the override file, or already discovered and added.
Known limitation, not a bug — see CLAUDE.md.

Federal depot records (see federal_depots.yaml / epa.py) already carry their
true account from ingestion and skip name-based bucketing entirely.

Facility resolution (Stage 1) runs independently within each account bucket,
not across the whole dataset — O(n^2) proximity checks stay bounded by
bucket size, and cross-account merges make no sense anyway (two different
companies should never end up in the same facility cluster). A hard size cap
exists as a safety valve against a pathologically generic name accidentally
bucketing thousands of unrelated small businesses together.

Writes real `facilities` and `accounts` DuckDB tables (per CLAUDE.md's
shared object model), not the anchors' in-memory report structure.
"""

import time
from collections import defaultdict

import duckdb
import yaml

from pipeline.config import DUCKDB_PATH
from pipeline.resolve.anchors import OVERRIDES_PATH, qualifies_for_tcv, resolve_facilities
from pipeline.resolve.geocode import geocode_batch
from pipeline.resolve.normalize import normalize_address, normalize_company_name

MAX_BUCKET_SIZE_FOR_RESOLUTION = 500  # safety valve against a pathologically generic shared name


def _load_overrides() -> dict:
    return yaml.safe_load(OVERRIDES_PATH.read_text())


def _effective_alias_map(overrides: dict) -> dict:
    """Explicit aliases from the yaml, PLUS a self-derived alias from every
    account's own canonical_name to its account key.

    Without this, an account key like OSHKOSH_CORPORATION never matches its
    own company's facility records: normalize_company_name("Oshkosh
    Corporation") strips "Corporation" as a legal suffix and produces
    "OSHKOSH", not "OSHKOSH_CORPORATION" — so Oshkosh's own directly-named
    plants would bucket separately from Pierce/JLG/McNeilus (which DO have
    explicit aliases pointing at OSHKOSH_CORPORATION), landing the parent
    company's own facilities in an unflagged, non-customer bucket. Caught by
    checking actual resolver output, not assumed to work from the code.
    Explicit aliases win on conflict (checked first, never overwritten)."""
    alias_map = dict(overrides.get("aliases", {}))
    for account_key, meta in overrides.get("accounts", {}).items():
        canonical = meta.get("canonical_name")
        if not canonical:
            continue
        norm = normalize_company_name(canonical)
        if norm and norm not in alias_map:
            alias_map[norm] = account_key
    return alias_map


def gather_all_epa(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute("""
        SELECT registry_id, facility_name, address, city, state, zip, latitude, longitude,
               voc_tons_2017, voc_tons_2020, voc_pct_change_2017_2020, voc_trend,
               pm10_tons_2020, pm25_tons_2020, has_air_major_permit,
               federal_depot_account, federal_depot_category
        FROM epa_facilities
    """).fetchall()
    cols = ["registry_id", "facility_name", "address", "city", "state", "zip", "latitude", "longitude",
            "voc_tons_2017", "voc_tons_2020", "voc_pct_change_2017_2020", "voc_trend",
            "pm10_tons_2020", "pm25_tons_2020", "has_air_major_permit",
            "federal_depot_account", "federal_depot_category"]
    out = []
    for row in rows:
        r = dict(zip(cols, row))
        out.append({
            "source": "epa", "source_id": r["registry_id"], "name": r["facility_name"],
            "address_line": r["address"], "city": r["city"], "state": r["state"], "zip": r["zip"],
            "lat": r["latitude"], "lon": r["longitude"],
            "federal_depot_account": r["federal_depot_account"], "federal_depot_category": r["federal_depot_category"],
            "extra": {k: r[k] for k in ("voc_tons_2017", "voc_tons_2020", "voc_pct_change_2017_2020",
                                         "voc_trend", "pm10_tons_2020", "pm25_tons_2020", "has_air_major_permit")},
        })
    return out


def gather_all_osha(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Collapsed to one row per unique establishment_id — see anchors.py for
    why (20,588 establishment-year rows is ~7,000 plants counted 3x)."""
    rows = con.execute("""
        SELECT establishment_id, establishment_name, company_name, street_address, city, state, zip_code,
               year, dart_rate, dart_cases, total_hours_worked, annual_average_employees
        FROM osha_establishments
        ORDER BY establishment_id, year
    """).fetchall()
    cols = ["establishment_id", "establishment_name", "company_name", "street_address", "city", "state",
            "zip_code", "year", "dart_rate", "dart_cases", "total_hours_worked", "annual_average_employees"]

    by_estab: dict[str, dict] = {}
    for row in rows:
        r = dict(zip(cols, row))
        eid = r["establishment_id"]
        if eid not in by_estab:
            by_estab[eid] = {
                "source": "osha", "source_id": eid, "name": r["establishment_name"],
                # company_name is the filer's legal-entity field, closer to an
                # account identity than establishment_name (often a site nickname)
                "account_name": r["company_name"] or r["establishment_name"],
                "address_line": r["street_address"], "city": r["city"], "state": r["state"],
                "zip": r["zip_code"], "lat": None, "lon": None,
                "federal_depot_account": None, "federal_depot_category": None,
                "extra": {"dart_series": [], "annual_average_employees": r["annual_average_employees"]},
            }
        by_estab[eid]["name"] = r["establishment_name"]
        by_estab[eid]["address_line"] = r["street_address"]
        by_estab[eid]["city"], by_estab[eid]["state"], by_estab[eid]["zip"] = r["city"], r["state"], r["zip_code"]
        by_estab[eid]["extra"]["annual_average_employees"] = r["annual_average_employees"]
        by_estab[eid]["extra"]["dart_series"].append(
            {"year": r["year"], "dart_rate": r["dart_rate"], "dart_cases": r["dart_cases"],
             "total_hours_worked": r["total_hours_worked"]}
        )
    return list(by_estab.values())


def gather_all_dod(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute("""
        SELECT award_id, recipient_name, pop_city, pop_state, award_amount, award_date, awarding_agency
        FROM dod_awards
    """).fetchall()
    cols = ["award_id", "recipient_name", "pop_city", "pop_state", "award_amount", "award_date", "awarding_agency"]
    return [dict(zip(cols, row)) for row in rows]


def assign_account_keys(records: list[dict], alias_map: dict, name_key: str = "name") -> None:
    for r in records:
        if r.get("federal_depot_account"):
            r["account_key"] = r["federal_depot_account"]
            continue
        norm = normalize_company_name(r.get("account_name") or r[name_key])
        r["account_key"] = alias_map.get(norm, norm) if norm else f"_unnamed_{r['source']}_{r['source_id']}"


def build_universe(con: duckdb.DuckDBPyConnection) -> None:
    overrides = _load_overrides()
    account_meta = overrides.get("accounts", {})

    t0 = time.time()
    epa = gather_all_epa(con)
    osha = gather_all_osha(con)
    dod = gather_all_dod(con)
    print(f"[resolve.universe] loaded {len(epa):,} EPA, {len(osha):,} OSHA (collapsed), {len(dod):,} DoD records ({time.time()-t0:.0f}s)")

    alias_map = _effective_alias_map(overrides)

    facility_records = epa + osha
    for r in facility_records:
        r["normalized_address"] = normalize_address(r["address_line"], r["city"], r["state"], r["zip"])
    assign_account_keys(facility_records, alias_map)
    assign_account_keys(dod, alias_map, name_key="recipient_name")

    print(f"[resolve.universe] geocoding OSHA records missing lat/lon (batch)")
    t0 = time.time()
    geocode_batch(facility_records)
    print(f"[resolve.universe] geocoding done ({time.time()-t0:.0f}s)")

    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in facility_records:
        buckets[r["account_key"]].append(r)
    print(f"[resolve.universe] {len(buckets):,} account buckets from {len(facility_records):,} facility records")

    dod_by_account: dict[str, list[dict]] = defaultdict(list)
    for d in dod:
        dod_by_account[d["account_key"]].append(d)

    facility_rows = []
    account_rows = []
    oversized_buckets = []
    t0 = time.time()
    for i, (account_key, records) in enumerate(buckets.items()):
        if len(records) > MAX_BUCKET_SIZE_FOR_RESOLUTION:
            oversized_buckets.append((account_key, len(records)))
            clusters = [{"member_idxs": [j], "tier": "singleton", "confidence": 1.0,
                         "reason": f"bucket size {len(records)} exceeds {MAX_BUCKET_SIZE_FOR_RESOLUTION}-record safety cap; resolution skipped for this account, treated as all-singleton",
                         "map_lat": records[j]["lat"], "map_lon": records[j]["lon"]}
                        for j in range(len(records))]
            pending = []
        else:
            clusters, pending = resolve_facilities(records)

        meta = account_meta.get(account_key, {})
        # account_is_customer means the logo is a customer -- it says nothing
        # about which plants have cells. That's facility-level
        # installed_status, which does NOT inherit from the account: every
        # facility defaults to "untouched" until we specifically know a given
        # plant has cells (we don't have that data yet, so it's untouched for
        # all of them right now, customer accounts included). Conflating the
        # two would silently zero out the untouched-qualified-TCV number at
        # every named customer account -- exactly backwards for a project
        # whose thesis is that expansion at existing customers is where the
        # money is.
        account_is_customer = bool(meta.get("account_is_customer", False))
        canonical_name = meta.get("canonical_name", records[0]["name"])
        vertical = meta.get("vertical")

        account_dod = dod_by_account.get(account_key, [])
        dod_by_city: dict[tuple, dict] = defaultdict(lambda: {"total_amount": 0.0, "award_count": 0})
        for d in account_dod:
            key = ((d["pop_city"] or "").upper(), (d["pop_state"] or "").upper())
            dod_by_city[key]["total_amount"] += d["award_amount"] or 0.0
            dod_by_city[key]["award_count"] += 1
        facility_city_idx: dict[tuple, list[int]] = defaultdict(list)
        for fi, c in enumerate(clusters):
            rep = records[c["member_idxs"][0]]
            facility_city_idx[((rep["city"] or "").upper(), (rep["state"] or "").upper())].append(fi)

        qualified_count = 0
        untouched_qualified_count = 0
        for fi, c in enumerate(clusters):
            qualified, qual_reason = qualifies_for_tcv(records, c)
            qualified_count += qualified
            rep = records[c["member_idxs"][0]]
            city_key = ((rep["city"] or "").upper(), (rep["state"] or "").upper())
            dod_here = dod_by_city.get(city_key)
            dod_amount = dod_here["total_amount"] if dod_here and len(facility_city_idx[city_key]) == 1 else None
            # No per-facility source tells us which specific plants already
            # have cells -- every facility starts "untouched" regardless of
            # whether its account is a customer. Flip this only when we
            # actually learn a given plant has cells, never by inheritance.
            facility_installed_status = "untouched"
            if qualified and facility_installed_status == "untouched":
                untouched_qualified_count += 1
            facility_rows.append({
                "facility_id": f"{account_key}::{fi}",
                "account_id": account_key,
                "facility_name": rep["name"],
                "address": rep["address_line"], "city": rep["city"], "state": rep["state"], "zip": rep["zip"],
                "latitude": c.get("map_lat"), "longitude": c.get("map_lon"),
                "sources": sorted({records[m]["source"] for m in c["member_idxs"]}),
                "member_source_ids": [f"{records[m]['source']}:{records[m]['source_id']}" for m in c["member_idxs"]],
                "match_tier": str(c["tier"]), "match_confidence": c["confidence"], "match_reason": c["reason"],
                "suspect_coordinates": bool(c.get("suspect_coordinate_idxs")),
                "voc_tons_2020": next((records[m]["extra"].get("voc_tons_2020") for m in c["member_idxs"] if records[m]["source"] == "epa" and records[m]["extra"].get("voc_tons_2020")), None),
                "has_air_major_permit": any(records[m]["extra"].get("has_air_major_permit") for m in c["member_idxs"] if records[m]["source"] == "epa"),
                "dod_awards_here_ttm": dod_amount,
                "qualified_for_tcv": qualified, "qualification_reason": qual_reason,
                "installed_status": facility_installed_status,
            })

        account_rows.append({
            "account_id": account_key,
            "legal_name": canonical_name,
            "vertical": vertical,
            "account_is_customer": account_is_customer,
            "total_facilities": len(clusters),
            "qualified_facilities": qualified_count,
            "untouched_qualified_facilities": untouched_qualified_count,
            "pending_review_count": len(pending),
        })

        if (i + 1) % 2000 == 0:
            print(f"[resolve.universe] {i+1:,}/{len(buckets):,} accounts resolved ({time.time()-t0:.0f}s elapsed)")

    if oversized_buckets:
        print(f"[resolve.universe] WARNING: {len(oversized_buckets)} bucket(s) exceeded the {MAX_BUCKET_SIZE_FOR_RESOLUTION}-record cap and were NOT proximity-resolved: {oversized_buckets}")

    import polars as pl

    facility_schema = {
        "facility_id": pl.Utf8, "account_id": pl.Utf8, "facility_name": pl.Utf8,
        "address": pl.Utf8, "city": pl.Utf8, "state": pl.Utf8, "zip": pl.Utf8,
        "latitude": pl.Float64, "longitude": pl.Float64,
        "sources": pl.List(pl.Utf8), "member_source_ids": pl.List(pl.Utf8),
        "match_tier": pl.Utf8, "match_confidence": pl.Float64, "match_reason": pl.Utf8,
        "suspect_coordinates": pl.Boolean, "voc_tons_2020": pl.Float64, "has_air_major_permit": pl.Boolean,
        "dod_awards_here_ttm": pl.Float64, "qualified_for_tcv": pl.Boolean, "qualification_reason": pl.Utf8,
        "installed_status": pl.Utf8,
    }
    account_schema = {
        "account_id": pl.Utf8, "legal_name": pl.Utf8, "vertical": pl.Utf8, "account_is_customer": pl.Boolean,
        "total_facilities": pl.Int64, "qualified_facilities": pl.Int64,
        "untouched_qualified_facilities": pl.Int64, "pending_review_count": pl.Int64,
    }
    facilities_df = pl.DataFrame(facility_rows, schema=facility_schema)
    accounts_df = pl.DataFrame(account_rows, schema=account_schema)

    con.register("facilities_out", facilities_df)
    con.execute("CREATE OR REPLACE TABLE facilities AS SELECT * FROM facilities_out")
    con.unregister("facilities_out")

    con.register("accounts_out", accounts_df)
    con.execute("CREATE OR REPLACE TABLE accounts AS SELECT * FROM accounts_out")
    con.unregister("accounts_out")

    print(f"[resolve.universe] wrote {len(facility_rows):,} facilities across {len(account_rows):,} accounts")
    customer_n = sum(1 for a in account_rows if a["account_is_customer"])
    qualified_n = sum(a["qualified_facilities"] for a in account_rows)
    untouched_qualified_n = sum(a["untouched_qualified_facilities"] for a in account_rows)
    customer_untouched_n = sum(a["untouched_qualified_facilities"] for a in account_rows if a["account_is_customer"])
    print(f"[resolve.universe] {customer_n} customer accounts; {qualified_n:,} qualified facilities universe-wide, {untouched_qualified_n:,} of them untouched")
    print(f"[resolve.universe] at customer accounts specifically: {customer_untouched_n:,} untouched qualified facilities -- the expansion opportunity")


def main() -> None:
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        build_universe(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
