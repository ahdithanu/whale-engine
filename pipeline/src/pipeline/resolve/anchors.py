"""Entity resolution scoped to the three anchor accounts (Boeing, Oshkosh
Corporation, Caterpillar) only — not the full universe. This is the proving
run: if the resolver can't group these three correctly, it isn't ready to run
against the full 35k+ EPA / 20k+ OSHA / 90k+ DoD record set, regardless of
what aggregate stats would say.

Stage 1 (facility resolution): EPA and OSHA records only. Address as the
primary signal (usaddress-normalized exact match = Tier 1), geocoded
proximity as a fallback (Tier 2), name similarity as a gate at every tier —
never a standalone signal, and never overridden by distance alone:
  Tier 1 (0.95): exact normalized address match
  Tier 2 (0.75): geocoded within 750m AND name similarity >= 0.6
  Tier 3: within 750m but name similarity < 0.6 -> NOT merged. Written out as
          a pending_review flag on both records; excluded from scoring until
          a human confirms. Distance alone is not a discriminator in either
          direction — 750m is wide enough that two unrelated companies in the
          same industrial park are a real, common case, and over-merging
          corrupts downstream scores in a way under-merging doesn't.

Stage 2 (corporate resolution): normalize company names (strip legal
suffixes), apply /pipeline/overrides/corporate_map.yaml for subsidiaries/DBAs
that don't share the parent's name, group into accounts.

DoD does NOT participate in Stage 1. USASpending's Primary Place of
Performance data is city/county/state/zip granularity only in what we
capture — confirmed empirically (see module-level constant DOD_POP_CHECK
docstring below) — so a DoD award can't be pinned to one specific facility.
Instead, DoD awards attach at the (account, city, state) level: summed and
attributed to whichever of that account's resolved facilities sit in that
city. If an account has more than one facility in the same city, the dollars
are a city-level signal shared across all of them, not evidence for any one
in particular — reported as such, not silently split.
"""

import json
from collections import defaultdict
from pathlib import Path

import duckdb
import yaml

from pipeline.config import DUCKDB_PATH, REPO_ROOT
from pipeline.resolve.geocode import geocode_oneline
from pipeline.resolve.normalize import (
    haversine_meters, matches_as_company, name_similarity, normalize_address, normalize_company_name,
)

OVERRIDES_PATH = REPO_ROOT / "pipeline" / "overrides" / "corporate_map.yaml"

PROXIMITY_METERS = 750
NAME_SIM_GATE = 0.6
MAX_CLUSTER_DIAMETER_METERS = 1_500  # single-linkage chaining can walk a cluster past this even with a 750m pairwise cap
COORDINATE_CONSENSUS_METERS = 100  # members within this of each other, for suspect-coordinate detection on Tier 1 clusters

DEFAULT_MIN_QUALIFYING_EMPLOYEES = 100  # a stated assumption, not derived — see qualification_sweep for sensitivity
QUALIFICATION_SWEEP_THRESHOLDS = [10, 50, 100, 250]

# Candidate search patterns per anchor account. Only BOEING / OSHKOSH /
# CATERPILLAR themselves are guaranteed hits; the rest are publicly known
# real subsidiaries included as candidates to search FOR, not assumed
# present — the run report below states which ones actually matched a source
# record and which didn't, and corporate_map.yaml is seeded only from the
# former.
CANDIDATE_PATTERNS = {
    "BOEING": ["BOEING", "MCDONNELL DOUGLAS", "INSITU", "JEPPESEN"],
    "OSHKOSH_CORPORATION": [
        "OSHKOSH", "PIERCE MANUFACTURING", "JLG INDUSTRIES", "MCNEILUS",
        "FRONTLINE COMMUNICATIONS", "PRATT MILLER",
    ],
    "CATERPILLAR": [
        "CATERPILLAR", "SOLAR TURBINES", "PROGRESS RAIL", "FG WILSON",
        "PERKINS ENGINES", "ELECTRO-MOTIVE",
    ],
}

EXPECTED_BOEING_CITIES = ["EVERETT", "RENTON", "CHARLESTON", "ST. LOUIS", "SAINT LOUIS", "MESA"]


def _load_overrides() -> dict:
    return yaml.safe_load(OVERRIDES_PATH.read_text())


def _save_overrides(data: dict) -> None:
    OVERRIDES_PATH.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _account_for_name(name: str) -> tuple[str | None, str | None]:
    """Company-identity matching, not substring search — see
    normalize.matches_as_company for why. Returns (account_key, pattern_hit)."""
    for account, patterns in CANDIDATE_PATTERNS.items():
        for p in patterns:
            if matches_as_company(name, p):
                return account, p
    return None, None


def gather_epa_candidates(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute("""
        SELECT registry_id, facility_name, address, city, state, zip, latitude, longitude,
               voc_tons_2017, voc_tons_2020, voc_pct_change_2017_2020, voc_trend,
               pm10_tons_2020, pm25_tons_2020, has_air_major_permit
        FROM epa_facilities
    """).fetchall()
    cols = ["registry_id", "facility_name", "address", "city", "state", "zip", "latitude", "longitude",
            "voc_tons_2017", "voc_tons_2020", "voc_pct_change_2017_2020", "voc_trend",
            "pm10_tons_2020", "pm25_tons_2020", "has_air_major_permit"]
    out = []
    for row in rows:
        r = dict(zip(cols, row))
        account, pattern_hit = _account_for_name(r["facility_name"])
        if account is None:
            continue
        out.append({
            "source": "epa", "source_id": r["registry_id"], "name": r["facility_name"],
            "address_line": r["address"], "city": r["city"], "state": r["state"], "zip": r["zip"],
            "lat": r["latitude"], "lon": r["longitude"], "account_guess": account, "pattern_hit": pattern_hit,
            "extra": {k: r[k] for k in ("voc_tons_2017", "voc_tons_2020", "voc_pct_change_2017_2020",
                                         "voc_trend", "pm10_tons_2020", "pm25_tons_2020", "has_air_major_permit")},
        })
    return out


def gather_osha_candidates(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Collapsed to one row per unique establishment_id before resolution —
    20,588 establishment-year rows is ~7,000 plants counted 3x; resolving on
    the raw rows would triple-count every OSHA-sourced facility."""
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
        account, pattern_hit = _account_for_name(r["establishment_name"])
        if account is None:
            continue
        eid = r["establishment_id"]
        if eid not in by_estab:
            by_estab[eid] = {
                "source": "osha", "source_id": eid, "name": r["establishment_name"],
                "address_line": r["street_address"], "city": r["city"], "state": r["state"],
                "zip": r["zip_code"], "lat": None, "lon": None, "account_guess": account, "pattern_hit": pattern_hit,
                "extra": {"dart_series": [], "annual_average_employees": r["annual_average_employees"]},
            }
        # later years overwrite name/address/employee-count with the most current filing
        by_estab[eid]["name"] = r["establishment_name"]
        by_estab[eid]["address_line"] = r["street_address"]
        by_estab[eid]["city"], by_estab[eid]["state"], by_estab[eid]["zip"] = r["city"], r["state"], r["zip_code"]
        by_estab[eid]["extra"]["annual_average_employees"] = r["annual_average_employees"]
        by_estab[eid]["extra"]["dart_series"].append(
            {"year": r["year"], "dart_rate": r["dart_rate"], "dart_cases": r["dart_cases"],
             "total_hours_worked": r["total_hours_worked"]}
        )
    return list(by_estab.values())


def gather_dod_candidates(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute("""
        SELECT award_id, recipient_name, pop_city, pop_state, award_amount, award_date, awarding_agency
        FROM dod_awards
    """).fetchall()
    cols = ["award_id", "recipient_name", "pop_city", "pop_state", "award_amount", "award_date", "awarding_agency"]
    out = []
    for row in rows:
        r = dict(zip(cols, row))
        account, pattern_hit = _account_for_name(r["recipient_name"])
        if account is None:
            continue
        r["account_guess"] = account
        r["pattern_hit"] = pattern_hit
        out.append(r)
    return out


def _which_patterns_hit(records: list[dict]) -> dict[str, set[str]]:
    hits: dict[str, set[str]] = defaultdict(set)
    for r in records:
        if r["account_guess"]:
            hits[r["account_guess"]].add(r["pattern_hit"])
    return hits


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _flag_suspect_coordinates(records: list[dict], idxs: list[int]) -> tuple[set[int], tuple]:
    """For a Tier 1 cluster (members share one exact normalized address by
    construction), any coordinate disagreement is a geocoding data-quality
    problem, not an identity question — the address already settled identity.
    Groups member coordinates into consensus clusters (within
    COORDINATE_CONSENSUS_METERS of each other); the largest group is treated
    as correct, everything else is flagged suspect. A tie (no single largest
    group) means we can't tell which is right, so everything is flagged.
    Returns (suspect_idxs, representative_lat_lon_or_None)."""
    geo = [(i, records[i]["lat"], records[i]["lon"]) for i in idxs if records[i]["lat"] is not None]
    if len(geo) < 2:
        return set(), (geo[0][1], geo[0][2]) if geo else None

    groups: list[list[tuple]] = []
    for i, lat, lon in geo:
        for g in groups:
            if haversine_meters(lat, lon, g[0][1], g[0][2]) <= COORDINATE_CONSENSUS_METERS:
                g.append((i, lat, lon))
                break
        else:
            groups.append([(i, lat, lon)])

    if len(groups) == 1:
        return set(), (groups[0][0][1], groups[0][0][2])

    groups.sort(key=len, reverse=True)
    if len(groups[0]) > len(groups[1]):
        suspect = {i for g in groups[1:] for i, _, _ in g}
        rep = (groups[0][0][1], groups[0][0][2])
    else:
        suspect = {i for i, _, _ in geo}
        rep = None
    return suspect, rep


def resolve_facilities(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """records must already carry normalized_address (may be None) and lat/lon
    (may be None, post-geocoding). Returns (facility_clusters, pending_review)."""
    n = len(records)
    uf = UnionFind(n)
    reasons: dict[int, list[str]] = defaultdict(list)
    pending_review = []

    by_addr: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if r["normalized_address"]:
            by_addr[r["normalized_address"]].append(i)
    for addr, idxs in by_addr.items():
        if len(idxs) > 1:
            base = idxs[0]
            for j in idxs[1:]:
                uf.union(base, j)
                reasons[j].append(f"Tier 1 (0.95): exact normalized address '{addr}' shared with {records[base]['source']}:{records[base]['name']}")
                reasons[base].append(f"Tier 1 (0.95): exact normalized address '{addr}' shared with {records[j]['source']}:{records[j]['name']}")

    geocoded = [i for i in range(n) if records[i]["lat"] is not None and records[i]["lon"] is not None]
    for a in range(len(geocoded)):
        for b in range(a + 1, len(geocoded)):
            i, j = geocoded[a], geocoded[b]
            if uf.find(i) == uf.find(j):
                continue
            dist = haversine_meters(records[i]["lat"], records[i]["lon"], records[j]["lat"], records[j]["lon"])
            if dist > PROXIMITY_METERS:
                continue
            sim = name_similarity(records[i]["name"], records[j]["name"])
            if sim >= NAME_SIM_GATE:
                uf.union(i, j)
                reasons[j].append(f"Tier 2 (0.75): {dist:.0f}m from {records[i]['source']}:{records[i]['name']}, name similarity {sim:.2f} >= {NAME_SIM_GATE}")
                reasons[i].append(f"Tier 2 (0.75): {dist:.0f}m from {records[j]['source']}:{records[j]['name']}, name similarity {sim:.2f} >= {NAME_SIM_GATE}")
            else:
                pending_review.append({
                    "record_a": f"{records[i]['source']}:{records[i]['source_id']} ({records[i]['name']})",
                    "record_b": f"{records[j]['source']}:{records[j]['source_id']} ({records[j]['name']})",
                    "distance_m": round(dist, 1),
                    "name_similarity": round(sim, 3),
                    "reason": f"{dist:.0f}m apart (within {PROXIMITY_METERS}m tier) but name similarity {sim:.2f} < {NAME_SIM_GATE} gate — not merged, kept as separate facilities pending manual review",
                })

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)

    facilities = []
    for idxs in groups.values():
        if len(idxs) == 1:
            i = idxs[0]
            facilities.append({
                "member_idxs": idxs, "tier": "singleton", "confidence": 1.0,
                "reason": "no other source record shares this address or is within 750m with matching name",
                "map_lat": records[i]["lat"], "map_lon": records[i]["lon"],
            })
            continue

        all_reasons = sorted({r for i in idxs for r in reasons[i]})
        tier = 1 if all("Tier 1" in r for r in all_reasons) else 2
        confidence = 0.95 if tier == 1 else 0.75

        if tier == 1:
            # Members share one exact normalized address by construction —
            # any coordinate disagreement is a geocoding data-quality problem
            # (bad lat/lon on one FRS record), not an identity question. Merge
            # on the address, flag the outlier coordinate(s) as suspect, and
            # give the cluster a representative point built only from the
            # consensus so a map view never plots the plant in the wrong
            # place. Do NOT queue this as pending_review — the address already
            # answered "is this the same facility," which is the question
            # pending_review exists for.
            suspect_idxs, rep_latlon = _flag_suspect_coordinates(records, idxs)
            facility = {"member_idxs": idxs, "tier": 1, "confidence": confidence, "reason": "; ".join(all_reasons)}
            if suspect_idxs:
                suspect_desc = ", ".join(f"{records[i]['source']}:{records[i]['source_id']} ({records[i]['lat']},{records[i]['lon']})" for i in suspect_idxs)
                facility["suspect_coordinate_idxs"] = suspect_idxs
                facility["reason"] += f" — coordinate mismatch within an exact-address match: {suspect_desc} disagree with the rest by >{COORDINATE_CONSENSUS_METERS}m; flagged suspect and excluded from map view, not treated as a separate facility"
            facility["map_lat"], facility["map_lon"] = rep_latlon if rep_latlon else (None, None)
            facilities.append(facility)
            continue

        # Tier 2: single-linkage chaining unions on PAIRWISE distance, so a
        # cluster can end up spanning far more than PROXIMITY_METERS
        # end-to-end even though every adjacent link in the chain is legal.
        # Here a wide diameter DOES mean the grouping itself is suspect (no
        # address to fall back on), so cap it and queue for review rather
        # than trying to optimally re-split the chain.
        geo_members = [i for i in idxs if records[i]["lat"] is not None and records[i]["lon"] is not None]
        max_dist, far_pair = 0.0, None
        for a in range(len(geo_members)):
            for b in range(a + 1, len(geo_members)):
                i, j = geo_members[a], geo_members[b]
                d = haversine_meters(records[i]["lat"], records[i]["lon"], records[j]["lat"], records[j]["lon"])
                if d > max_dist:
                    max_dist, far_pair = d, (i, j)

        if max_dist > MAX_CLUSTER_DIAMETER_METERS:
            i, j = far_pair
            pending_review.append({
                "record_a": f"{records[i]['source']}:{records[i]['source_id']} ({records[i]['name']})",
                "record_b": f"{records[j]['source']}:{records[j]['source_id']} ({records[j]['name']})",
                "distance_m": round(max_dist, 1),
                "name_similarity": None,
                "reason": f"cluster diameter {max_dist:.0f}m > {MAX_CLUSTER_DIAMETER_METERS}m cap (chained through intermediate {PROXIMITY_METERS}m links) — whole cluster of {len(idxs)} records queued for review, not auto-resolved",
            })
            facilities.append({
                "member_idxs": idxs, "tier": "oversized_chain", "confidence": 0.4,
                "reason": f"{'; '.join(all_reasons)} — BUT cluster diameter {max_dist:.0f}m exceeds the {MAX_CLUSTER_DIAMETER_METERS}m cap; queued for review rather than auto-resolved",
                "map_lat": None, "map_lon": None,
            })
        else:
            centroid_lat = sum(records[i]["lat"] for i in geo_members) / len(geo_members) if geo_members else None
            centroid_lon = sum(records[i]["lon"] for i in geo_members) / len(geo_members) if geo_members else None
            facilities.append({
                "member_idxs": idxs, "tier": tier, "confidence": confidence, "reason": "; ".join(all_reasons),
                "map_lat": centroid_lat, "map_lon": centroid_lon,
            })

    return facilities, pending_review


def qualifies_for_tcv(records: list[dict], cluster: dict, min_employees: int = DEFAULT_MIN_QUALIFYING_EMPLOYEES) -> tuple[bool, str]:
    """A resolved facility counts toward est_account_TCV_ceiling only if it
    shows real physical finishing/manufacturing activity, not just an EPA
    sub-registration, sales office, or admin address. Boeing resolving to 149
    facilities is mostly labs/offices/registrations — fine for a facility
    count, nonsense as 149 TCV-bearing plants.

    Qualifies if ANY member record has:
      - nonzero VOC tonnage (2017 or 2020), or
      - an AIR MAJOR (Title V proxy) permit, or
      - an OSHA record with annual_average_employees >= min_employees

    min_employees is a stated assumption, not derived from any spec — a
    10-person site isn't absorbing a $600K robotic cell, so the default is
    100; see qualification_sweep() for how the qualified count moves across
    10/50/100/250 before committing to one number for scoring."""
    for i in cluster["member_idxs"]:
        r = records[i]
        if r["source"] == "epa":
            voc17 = r["extra"].get("voc_tons_2017") or 0
            voc20 = r["extra"].get("voc_tons_2020") or 0
            if voc17 > 0 or voc20 > 0:
                return True, f"nonzero VOC tonnage ({r['source']}:{r['name']}, 2017={voc17}, 2020={voc20})"
            if r["extra"].get("has_air_major_permit"):
                return True, f"AIR MAJOR (Title V proxy) permit ({r['source']}:{r['name']})"
        elif r["source"] == "osha":
            emp = r["extra"].get("annual_average_employees")
            if emp is not None and emp >= min_employees:
                return True, f"OSHA annual_average_employees={emp} >= {min_employees} ({r['source']}:{r['name']})"
    return False, f"no nonzero VOC tonnage, no AIR MAJOR permit, no OSHA record with >= {min_employees} employees"


def qualification_sweep(records: list[dict], clusters: list[dict], thresholds: list[int] = QUALIFICATION_SWEEP_THRESHOLDS) -> dict[int, int]:
    """Clustering doesn't depend on the employee threshold, only the
    qualification flag does — so re-derive counts per threshold cheaply
    without re-running resolution."""
    return {
        t: sum(1 for c in clusters if qualifies_for_tcv(records, c, min_employees=t)[0])
        for t in thresholds
    }


def geocode_missing(records: list[dict]) -> None:
    to_geocode = [r for r in records if r["lat"] is None and r["address_line"]]
    if to_geocode:
        print(f"[resolve.anchors] geocoding {len(to_geocode)} records missing lat/lon (Census single-address, paced)")
    for r in to_geocode:
        result = geocode_oneline(r["address_line"], r["city"], r["state"], r["zip"])
        if result:
            r["lat"], r["lon"] = result


def assign_accounts(records: list[dict], overrides: dict) -> None:
    """Applies corporate_map.yaml aliases on top of the pattern-based
    account_guess, so the report reflects Stage 2 (override-aware) resolution
    even though for this anchor-scoped run the pattern match already implies
    the account."""
    alias_map = {k: v for k, v in overrides.get("aliases", {}).items()}
    for r in records:
        norm = normalize_company_name(r["name"])
        r["resolved_account"] = alias_map.get(norm, r["account_guess"])


def attach_dod(account_key: str, facility_clusters: list[dict], records: list[dict], dod_candidates: list[dict]) -> dict:
    account_dod = [d for d in dod_candidates if d["account_guess"] == account_key]
    by_city: dict[tuple, dict] = defaultdict(lambda: {"total_amount": 0.0, "award_count": 0})
    for d in account_dod:
        key = ((d["pop_city"] or "").upper(), (d["pop_state"] or "").upper())
        by_city[key]["total_amount"] += d["award_amount"] or 0.0
        by_city[key]["award_count"] += 1

    facility_cities: dict[tuple, list[int]] = defaultdict(list)
    for fi, cluster in enumerate(facility_clusters):
        rep = records[cluster["member_idxs"][0]]
        key = ((rep["city"] or "").upper(), (rep["state"] or "").upper())
        facility_cities[key].append(fi)

    attribution = {}
    for city_key, agg in by_city.items():
        matching_facility_idxs = facility_cities.get(city_key, [])
        attribution[city_key] = {
            "total_amount": agg["total_amount"],
            "award_count": agg["award_count"],
            "matching_facility_count": len(matching_facility_idxs),
            "facility_idxs": matching_facility_idxs,
            "note": (
                "shared city-level signal across multiple facilities, not attributable to one in particular"
                if len(matching_facility_idxs) > 1
                else ("attached to the single resolved facility in this city" if matching_facility_idxs
                      else "no resolved facility in this city — award exists but can't be pinned to a plant")
            ),
        }
    return attribution


def run() -> dict:
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    overrides = _load_overrides()

    epa = gather_epa_candidates(con)
    osha = gather_osha_candidates(con)
    dod = gather_dod_candidates(con)
    con.close()

    print(f"[resolve.anchors] candidates: {len(epa)} EPA, {len(osha)} OSHA (collapsed), {len(dod)} DoD awards")

    epa_hits = _which_patterns_hit(epa)
    osha_hits = _which_patterns_hit(osha)
    dod_hits = _which_patterns_hit(dod)
    all_hit_patterns = defaultdict(set)
    for hits in (epa_hits, osha_hits, dod_hits):
        for account, patterns in hits.items():
            all_hit_patterns[account] |= patterns

    for account, patterns in CANDIDATE_PATTERNS.items():
        found = all_hit_patterns.get(account, set())
        not_found = set(patterns) - found
        print(f"[resolve.anchors] {account}: patterns matched = {sorted(found)}; searched-but-not-found = {sorted(not_found)}")

    facility_records = epa + osha
    for r in facility_records:
        r["normalized_address"] = normalize_address(r["address_line"], r["city"], r["state"], r["zip"])
    geocode_missing(facility_records)
    assign_accounts(facility_records, overrides)
    for d in dod:
        norm = normalize_company_name(d["recipient_name"])
        d["resolved_account"] = overrides.get("aliases", {}).get(norm, d["account_guess"])

    results = {}
    for account_key in CANDIDATE_PATTERNS:
        account_records = [r for r in facility_records if r["account_guess"] == account_key]
        clusters, pending = resolve_facilities(account_records)
        for c in clusters:
            c["qualified"], c["qualification_reason"] = qualifies_for_tcv(account_records, c)
        dod_attribution = attach_dod(account_key, clusters, account_records, dod)
        qualified_count = sum(1 for c in clusters if c["qualified"])
        sweep = qualification_sweep(account_records, clusters)
        results[account_key] = {
            "records": account_records,
            "clusters": clusters,
            "pending_review": pending,
            "dod_attribution": dod_attribution,
            "total_facilities": len(clusters),
            "qualified_facilities": qualified_count,
            "qualification_sweep": sweep,
        }
        sweep_desc = ", ".join(f"{t}={n}" for t, n in sweep.items())
        print(f"[resolve.anchors] {account_key}: {len(clusters)} total facilities, {qualified_count} qualified for TCV (default threshold {DEFAULT_MIN_QUALIFYING_EMPLOYEES}); sweep: {sweep_desc}")

    return results


if __name__ == "__main__":
    run()
