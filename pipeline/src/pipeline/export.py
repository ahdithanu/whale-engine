"""Exports the scored, resolved dataset to JSON for the Next.js dashboard
(/web) to consume as static assets under web/public/data/ — no live DuckDB
connection needed at request time for the main dashboard load. Per the
"must feel instant, ship filtered JSON" requirement: this is filtered to the
"interesting" universe (accounts with at least one qualified facility, or
flagged account_is_customer), not the full ~41K-account resolved universe.

Signal shape note: the dashboard design has exactly 5 signal sliders
(VOC / Trend / DART / DoD / Size). The real scoring model underneath has SIX
weighted components (VOC level, VOC trend, DART rate+trend blended, DoD,
employee count, air permit) — Size blends employee_component and
air_permit_component (fixed 60/40, matching their original 0.15:0.10 weight
ratio) into one slider-facing score, so the design's 5-slot layout holds
without adding a component that isn't there. See CLAUDE.md / score.py for why
VOC trend is a real weighted signal (not just why-now flavor text) and why
Size shows employee count + permit class instead of square footage (no real
source gives building square footage anywhere in EPA/OSHA/DoD).
"""

import csv
import json
from datetime import datetime, timezone

import duckdb

from pipeline.config import DATA_DIR, DUCKDB_PATH, REPO_ROOT
from pipeline.universe import load_universe

EXPORT_DIR = REPO_ROOT / "web" / "public" / "data"
ENRICHMENT_CACHE_PATH = DATA_DIR / "enrichment" / "accounts.json"
# Join point for a real CRM export, not a real data source yet -- no CRM
# integration exists, so this file normally doesn't exist and every facility
# exports crm_status=null (see the PROVENANCE note in web/app/page.tsx: the
# installed/pipeline claim is inferred from the public logo wall today, not
# measured). If/when a CRM export becomes available, drop a CSV with columns
# facility_id,crm_status ("installed" | "in_pipeline" | "untouched") here --
# no code change required.
CRM_STATUS_OVERRIDE_PATH = REPO_ROOT / "pipeline" / "overrides" / "crm_status.csv"

SIZE_EMPLOYEE_SUBWEIGHT = 0.6  # matches employee_count_band(0.15) : air_permit_class(0.10) = 60:40
SIZE_PERMIT_SUBWEIGHT = 0.4


def _vertical_name_map() -> dict[str, str]:
    universe = load_universe()
    return {k: v.name for k, v in universe.verticals.items()}


def _dominant_vertical(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Most common facility_vertical among each account's facilities, for
    display in the account table -- accounts.vertical itself is only set for
    the ~17 named accounts in corporate_map.yaml."""
    rows = con.execute("""
        SELECT account_id, facility_vertical, count(*) as n
        FROM facilities
        WHERE facility_vertical IS NOT NULL
        GROUP BY account_id, facility_vertical
        QUALIFY row_number() OVER (PARTITION BY account_id ORDER BY n DESC) = 1
    """).fetchall()
    return {r[0]: r[1] for r in rows}


def _load_enrichment_cache() -> dict[str, dict]:
    """account_id -> Apollo enrichment record (built by pipeline.enrich_accounts,
    top 50 accounts by untouched_tcv only). Empty dict, not an error, if the
    cache hasn't been built yet -- enrichment fields are additive, never
    required for the export to run."""
    if not ENRICHMENT_CACHE_PATH.exists():
        return {}
    return json.loads(ENRICHMENT_CACHE_PATH.read_text())


def _epa_registry_names(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """registry_id -> EPA FRS facility_name, for the display-name resolver's
    first fallback (web/lib/displayName.ts) when a resolved facility's own
    name is OSHA free-text junk (a bare address, a site code, a generic
    label like "Plant 1") -- the EPA FRS name is filed by the facility
    itself on a federal registration, not typed freehand into an injury
    report form, so it's the more reliable of the two raw name sources."""
    rows = con.execute("SELECT registry_id, facility_name FROM epa_facilities").fetchall()
    return {r[0]: r[1] for r in rows}


def _load_crm_status_overrides() -> dict[str, str]:
    """facility_id -> crm_status, from an optional hand-supplied CSV. Empty
    dict, not an error, if the file doesn't exist -- there is no CRM
    integration today, so this is normally empty and every facility exports
    crm_status=null. See CRM_STATUS_OVERRIDE_PATH above."""
    if not CRM_STATUS_OVERRIDE_PATH.exists():
        return {}
    with CRM_STATUS_OVERRIDE_PATH.open(newline="") as f:
        return {row["facility_id"]: row["crm_status"] for row in csv.DictReader(f)}


def export_accounts(con: duckdb.DuckDBPyConnection, vertical_names: dict[str, str]) -> list[dict]:
    dominant = _dominant_vertical(con)
    enrichment = _load_enrichment_cache()
    rows = con.execute("""
        SELECT account_id, legal_name, vertical, account_is_customer,
               total_facilities, qualified_facilities, untouched_qualified_facilities,
               pending_review_count, est_account_tcv_ceiling
        FROM accounts
        WHERE qualified_facilities > 0 OR account_is_customer
        ORDER BY est_account_tcv_ceiling DESC
    """).fetchall()
    cols = ["account_id", "legal_name", "vertical", "account_is_customer",
            "total_facilities", "qualified_facilities", "untouched_qualified_facilities",
            "pending_review_count", "est_account_tcv_ceiling"]
    out = []
    for row in rows:
        r = dict(zip(cols, row))
        vkey = r["vertical"] or dominant.get(r["account_id"])
        enr = enrichment.get(r["account_id"])
        out.append({
            "account_id": r["account_id"],
            "legal_name": r["legal_name"],
            "vertical_key": vkey,
            "vertical_name": vertical_names.get(vkey, vkey or "Unclassified"),
            "account_is_customer": r["account_is_customer"],
            "total_facilities": r["total_facilities"],
            "qualified_facilities": r["qualified_facilities"],
            "untouched_qualified_facilities": r["untouched_qualified_facilities"],
            "pending_review_count": r["pending_review_count"],
            # installed_tcv/pipeline_tcv are always 0 today -- no facility has
            # ever been marked anything but "untouched" (see CLAUDE.md: no
            # per-facility source tells us which plants already have cells).
            # Real, not a placeholder: this IS the current state of the data.
            "installed_tcv": 0.0,
            "pipeline_tcv": 0.0,
            "untouched_tcv": r["est_account_tcv_ceiling"],
            # Apollo company enrichment (pipeline.enrich_accounts), top 50
            # accounts by untouched_tcv only -- null for everyone else, and
            # null for the top 50 accounts Apollo couldn't confidently match
            # (see enrich_accounts_data.UNMATCHED for why, per account).
            "website_url": enr["website_url"] if enr and enr["matched"] else None,
            "linkedin_url": enr["linkedin_url"] if enr and enr["matched"] else None,
            "hq_city": enr["hq_city"] if enr and enr["matched"] else None,
            "hq_state": enr["hq_state"] if enr and enr["matched"] else None,
            "apollo_employee_count": enr["apollo_employee_count"] if enr and enr["matched"] else None,
        })
    return out


def export_facilities(con: duckdb.DuckDBPyConnection, vertical_names: dict[str, str]) -> list[dict]:
    crm_status_overrides = _load_crm_status_overrides()
    epa_registry_names = _epa_registry_names(con)
    rows = con.execute("""
        SELECT facility_id, account_id, facility_name, city, state, latitude, longitude,
               suspect_coordinates, facility_vertical, match_tier, match_confidence, match_reason,
               voc_tons_2020, voc_percentile, voc_score, voc_trend, voc_trend_score,
               dart_rate, dart_rate_percentile, dart_rate_score, dart_trend, dart_trend_score,
               dod_awards_here_ttm, dod_city_shared, dod_score, dod_percentile,
               employee_count, employee_count_suspect, employee_score,
               has_air_major_permit, air_permit_score,
               qualified_for_tcv, qualification_reason, installed_status,
               est_cells_capacity, est_facility_tcv, est_finishing_headcount,
               tcv_basis, tcv_is_derived, facility_score, why_now,
               member_source_ids, sources
        FROM facilities
        WHERE account_id IN (
            SELECT account_id FROM accounts WHERE qualified_facilities > 0 OR account_is_customer
        )
    """).fetchall()
    cols = ["facility_id", "account_id", "facility_name", "city", "state", "latitude", "longitude",
            "suspect_coordinates", "facility_vertical", "match_tier", "match_confidence", "match_reason",
            "voc_tons_2020", "voc_percentile", "voc_score", "voc_trend", "voc_trend_score",
            "dart_rate", "dart_rate_percentile", "dart_rate_score", "dart_trend", "dart_trend_score",
            "dod_awards_here_ttm", "dod_city_shared", "dod_score", "dod_percentile",
            "employee_count", "employee_count_suspect", "employee_score",
            "has_air_major_permit", "air_permit_score",
            "qualified_for_tcv", "qualification_reason", "installed_status",
            "est_cells_capacity", "est_facility_tcv", "est_finishing_headcount",
            "tcv_basis", "tcv_is_derived", "facility_score", "why_now",
            "member_source_ids", "sources"]

    out = []
    for row in rows:
        r = dict(zip(cols, row))
        size_score = (r["employee_score"] or 0.0) * SIZE_EMPLOYEE_SUBWEIGHT + (r["air_permit_score"] or 0.0) * SIZE_PERMIT_SUBWEIGHT
        permit_label = "Title V major source" if r["has_air_major_permit"] else "Minor / synthetic minor / unknown"
        size_raw = (
            (f"{r['employee_count']:.0f} employees (banded)" if r["employee_count"] is not None else "Employee count unavailable")
            + f" · {permit_label}"
        )
        # First fallback for the display-name resolver (web/lib/displayName.ts)
        # when facility_name is OSHA free-text junk. None if this facility has
        # no EPA member at all.
        epa_frs_name = next(
            (epa_registry_names.get(sid.split(":", 1)[1]) for sid in r["member_source_ids"] if sid.startswith("epa:")),
            None,
        )
        out.append({
            "facility_id": r["facility_id"],
            "account_id": r["account_id"],
            "facility_name": r["facility_name"],
            "epa_frs_name": epa_frs_name,
            "city": r["city"], "state": r["state"],
            "latitude": r["latitude"], "longitude": r["longitude"],
            "suspect_coordinates": r["suspect_coordinates"],
            "facility_vertical_key": r["facility_vertical"],
            "facility_vertical_name": vertical_names.get(r["facility_vertical"], r["facility_vertical"] or "Unclassified"),
            "match_tier": r["match_tier"], "match_confidence": r["match_confidence"], "match_reason": r["match_reason"],
            "qualified_for_tcv": r["qualified_for_tcv"], "qualification_reason": r["qualification_reason"],
            "installed_status": r["installed_status"],
            # Real CRM join point, not real CRM data -- see
            # CRM_STATUS_OVERRIDE_PATH above. Null for every facility until a
            # CRM export actually exists.
            "crm_status": crm_status_overrides.get(r["facility_id"]),
            "est_cells_capacity": r["est_cells_capacity"], "est_facility_tcv": r["est_facility_tcv"],
            "est_finishing_headcount": r["est_finishing_headcount"],
            "tcv_basis": r["tcv_basis"], "tcv_is_derived": r["tcv_is_derived"],
            "facility_score": r["facility_score"], "why_now": r["why_now"],
            "member_source_ids": r["member_source_ids"], "sources": r["sources"],
            # Five signal groups, matching the dashboard's five sliders exactly.
            # `score` is always 0-100 (never null) for client-side reweighting;
            # `present` is the honesty signal -- whether that score reflects
            # real underlying data or the uniform "missing data = 0" rule.
            "signals": {
                "voc": {
                    "score": r["voc_score"], "present": r["voc_percentile"] is not None,
                    "raw": f"{r['voc_tons_2020']:.1f} tons/yr VOC · P{round(r['voc_percentile'])} in vertical" if r["voc_percentile"] is not None else "No NEI match for this facility",
                    "source": "EPA NATIONAL EMISSIONS INVENTORY 2020",
                },
                "trend": {
                    "score": r["voc_trend_score"], "present": r["voc_trend"] != "no_data",
                    "raw": f"VOC trend 2017→2020: {r['voc_trend'].upper()}" if r["voc_trend"] != "no_data" else "No 2017-and-2020 NEI match to compute a trend",
                    "source": "EPA NEI 2017 / 2020 DELTA",
                },
                "dart": {
                    "score": r["dart_rate_score"], "present": r["dart_rate_percentile"] is not None,
                    "raw": f"DART {r['dart_rate']:.1f} · P{round(r['dart_rate_percentile'])} in vertical · {r['dart_trend'].upper()}" if r["dart_rate_percentile"] is not None else "No OSHA ITA match for this facility",
                    "source": "OSHA INJURY TRACKING APPLICATION",
                },
                "dod": {
                    "score": r["dod_score"], "present": r["dod_awards_here_ttm"] is not None,
                    "raw": f"${r['dod_awards_here_ttm']:,.0f} obligated, trailing 24 months" if r["dod_awards_here_ttm"] is not None else "No DoD prime awards at this place of performance",
                    "source": "USASPENDING FEDERAL AWARDS",
                    "note": "CITY-LEVEL ATTRIBUTION — NOT STREET-ADDRESS PRECISE" if r["dod_city_shared"] else None,
                },
                "size": {
                    "score": round(size_score, 1), "present": r["employee_count"] is not None,
                    "raw": size_raw,
                    "source": "OSHA ITA · EPA FACILITY REGISTRY SERVICE",
                },
            },
        })
    return out


def export_pending_review(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute("""
        SELECT pr.account_id, a.legal_name, pr.record_a, pr.record_b, pr.distance_m, pr.name_similarity, pr.reason
        FROM pending_review pr
        JOIN accounts a USING (account_id)
    """).fetchall()
    cols = ["account_id", "legal_name", "record_a", "record_b", "distance_m", "name_similarity", "reason"]
    return [dict(zip(cols, row)) for row in rows]


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        vertical_names = _vertical_name_map()
        accounts = export_accounts(con, vertical_names)
        facilities = export_facilities(con, vertical_names)
        pending_review = export_pending_review(con)

        account_ids_with_facilities = {f["account_id"] for f in facilities}
        facility_ids_by_account = {}
        for f in facilities:
            facility_ids_by_account.setdefault(f["account_id"], []).append(f["facility_id"])

        meta = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_accounts_exported": len(accounts),
            "total_facilities_exported": len(facilities),
            "customer_accounts": sum(1 for a in accounts if a["account_is_customer"]),
            "total_untouched_tcv": sum(a["untouched_tcv"] for a in accounts),
            "customer_untouched_tcv": sum(a["untouched_tcv"] for a in accounts if a["account_is_customer"]),
            "total_qualified_facilities": sum(a["qualified_facilities"] for a in accounts),
            "pending_review_count": len(pending_review),
        }

        (EXPORT_DIR / "accounts.json").write_text(json.dumps(accounts))
        (EXPORT_DIR / "facilities.json").write_text(json.dumps(facilities))
        (EXPORT_DIR / "pending_review.json").write_text(json.dumps(pending_review))
        (EXPORT_DIR / "meta.json").write_text(json.dumps(meta, indent=2))

        print(f"[export] wrote {len(accounts):,} accounts, {len(facilities):,} facilities, "
              f"{len(pending_review):,} pending_review entries to {EXPORT_DIR}")
        print(f"[export] meta: {meta}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
