"""Facility Signal Engine scoring: a 0-100 signal score per resolved facility
from six weighted inputs, plus a deterministic (not LLM-generated) "why now"
string. Weights and dollar/ratio assumptions live in scoring_config.yaml, not
here — see that file for why each knob is what it is, including why VOC
tonnage and VOC trend are two separate weighted signals (a later addition,
not the original 5-signal spec) rather than one.

Percentile-based components (VOC tonnage, DART rate) are ranked WITHIN
VERTICAL, not globally: a 400-ton VOC facility means something different in
aerospace than in metal fabrication, and comparing across verticals nationally
would just reward whichever vertical happens to run hotter industrially.
Facilities with no determinable vertical (federal depot facilities, which
bypassed NAICS matching entirely — see CLAUDE.md) get 0 on both
vertical-scoped components, on top of the OSHA/EPA reporting-granularity gap
already documented there. This is a real, compounding reason federal
facilities score low — not something this module tries to paper over.

DoD dollars are ranked globally (not per-vertical): government contracting
volume isn't a function of physical plant scale the way VOC/DART are, so
there's no vertical-relative baseline to compare against.

Missing data always contributes 0 to its weighted component, never gets
excluded-and-renormalized. A facility scoring low because a signal is simply
absent (no NEI match, no OSHA record) should read as "we don't know", visible
in the score breakdown and the why_now string omitting that clause — not
quietly boosted by only being judged on the signals it happens to have.

TCV fallback chain: a facility can qualify for TCV via VOC, AIR MAJOR permit,
or OSHA employee count, but est_facility_TCV specifically needs a headcount
estimate to derive cells. Requiring a real OSHA employee_count match zeroed
out most qualified facilities that DID qualify some other way (Caterpillar:
9 of 10 qualified facilities, $0 TCV each, because only 1 had an OSHA match).
Every qualified facility now gets a nonzero cell estimate via a fallback
chain, ordered from most to least facility-specific:
  1. actual OSHA employee_count (unchanged, most trustworthy)
  2. VOC-tonnage-calibrated estimate (per-vertical ratio, from facilities
     that have BOTH a real employee count and real VOC data)
  3. PM10-tonnage-calibrated estimate (same idea, different pollutant, for
     facilities with no VOC match)
  4. the vertical's own median cells-per-qualified-facility (no
     facility-specific data needed, just "this is a typical plant in this
     vertical")
There is deliberately no floor beyond rung 4. A facility whose estimated
finishing headcount doesn't clear one cell's worth of labor (est_cells_capacity
rounds DOWN, not up) gets est_cells_capacity=0 and est_facility_TCV=$0 --
that is a real fact about the facility (too small to plausibly absorb a
$600K cell), not a gap to paper over. An earlier version forced every
qualified facility to at least 1 cell via ceil()+max(1,...); that silently
manufactured $600K+ of TCV for facilities like a sub-1-headcount site,
which is exactly the kind of invented figure this project's "every number
must be traceable to a source" principle rules out.
Every facility's `tcv_basis` records which rung produced its estimate;
`tcv_is_derived` is true for anything other than rung 1, so the UI can flag
it rather than presenting a fallback estimate as a measured fact.
"""

import math
from pathlib import Path

import duckdb
import polars as pl
import yaml

from pipeline.config import DUCKDB_PATH, REPO_ROOT
from pipeline.universe import load_universe

SCORING_CONFIG_PATH = Path(__file__).parent / "scoring_config.yaml"

MIN_CALIBRATION_FACILITIES = 3  # don't trust a per-vertical ratio computed from 1-2 data points


def load_scoring_config() -> dict:
    config = yaml.safe_load(SCORING_CONFIG_PATH.read_text())
    total = sum(config["weights"].values())
    assert abs(total - 1.0) < 1e-6, f"scoring weights must sum to 1.0, got {total}"
    return config


def _percentile_within_group(df: pl.DataFrame, value_col: str, group_col: str, out_col: str) -> pl.DataFrame:
    """0-100 percentile rank of value_col within each group_col group.
    Rows with null value_col or null group_col get null (not 0 — 0 is applied
    later, uniformly, as the "missing data" contribution rule)."""
    return df.with_columns(
        pl.when(pl.col(value_col).is_not_null() & pl.col(group_col).is_not_null())
        .then((pl.col(value_col).rank(method="average").over(group_col) - 1)
              / (pl.col(value_col).count().over(group_col) - 1).clip(lower_bound=1) * 100)
        .otherwise(None)
        .alias(out_col)
    )


def _percentile_global(df: pl.DataFrame, value_col: str, out_col: str) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col(value_col).is_not_null())
        .then((pl.col(value_col).rank(method="average") - 1) / (pl.col(value_col).count() - 1).clip(lower_bound=1) * 100)
        .otherwise(None)
        .alias(out_col)
    )


def _employee_band_score(employee_count: float | None, bands: list[dict]) -> float:
    if employee_count is None:
        return 0.0
    for band in bands:
        if band["max"] is None or employee_count <= band["max"]:
            return float(band["score"])
    return float(bands[-1]["score"])


def _facility_magnitude(employee_score: float, voc_percentile: float | None, has_air_major_permit: bool) -> float:
    """Unweighted average of three already-computed 0-100 signals -- how big
    is this physical facility, independent of which TCV-fallback rung
    produced its headcount estimate. Missing voc_percentile contributes 0,
    same "missing data = 0, never renormalized" rule as everywhere else in
    this module."""
    air_score = 100.0 if has_air_major_permit else 0.0
    return (employee_score + (voc_percentile or 0.0) + air_score) / 3


def _max_cells_for_magnitude(magnitude: float, tiers: list[dict]) -> int:
    """Piecewise-linear interpolation between the configured tier control
    points (sorted ascending by min_magnitude), floored to an integer cell
    count. See scoring_config.yaml for why this is interpolated rather than
    a flat step per tier -- a flat step reproduced the original flat-cap
    collision bug, just at four values instead of one."""
    points = sorted(tiers, key=lambda t: t["min_magnitude"])
    if magnitude <= points[0]["min_magnitude"]:
        return int(points[0]["max_cells"])
    if magnitude >= points[-1]["min_magnitude"]:
        return int(points[-1]["max_cells"])
    for lo, hi in zip(points, points[1:]):
        if lo["min_magnitude"] <= magnitude <= hi["min_magnitude"]:
            span = hi["min_magnitude"] - lo["min_magnitude"]
            frac = (magnitude - lo["min_magnitude"]) / span if span else 0.0
            interpolated = lo["max_cells"] + frac * (hi["max_cells"] - lo["max_cells"])
            return int(math.floor(interpolated))
    return int(points[-1]["max_cells"])


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _why_now(row: dict, vertical_name: str | None, dart_median: float | None) -> str:
    clauses = []
    if row["has_air_major_permit"]:
        clauses.append("Title V air permit")
    if row["voc_percentile"] is not None and vertical_name:
        clauses.append(f"{_ordinal(round(row['voc_percentile']))} percentile VOC in {vertical_name}")
    if row["dart_rate"] is not None:
        if dart_median and dart_median > 0:
            multiple = row["dart_rate"] / dart_median
            trend_word = {"up": "and rising", "down": "and falling", "flat": "and flat", "no_data": ""}.get(row["dart_trend"], "")
            clauses.append(f"DART rate {multiple:.1f}x sector median {trend_word}".strip())
        else:
            clauses.append(f"DART rate {row['dart_rate']:.1f}")
    if row["dod_awards_here_ttm"]:
        city_note = " (city-level attribution)" if row.get("dod_city_shared") else ""
        clauses.append(f"${row['dod_awards_here_ttm']:,.0f} in DoD awards at this location over 24 months{city_note}")
    if row["est_finishing_headcount"]:
        derived_note = " (estimated)" if row.get("tcv_is_derived") else ""
        clauses.append(f"Estimated {row['est_finishing_headcount']:.0f} finishing headcount{derived_note}")
    if not clauses:
        return "No qualifying signal data available for this facility."
    return ". ".join(clauses) + "."


def _calibration_ratios(rows: list[dict], value_key: str) -> dict[str, float]:
    """median(actual finishing headcount / value_key) per vertical, among
    facilities with a REAL (non-derived) headcount and a present value_key.
    Used to calibrate the VOC- and PM-based TCV fallback rungs."""
    by_vertical: dict[str, list[float]] = {}
    for r in rows:
        v = r.get(value_key)
        h = r.get("_actual_finishing_headcount")
        if v is None or h is None or v <= 0.1 or not r.get("facility_vertical"):
            continue
        by_vertical.setdefault(r["facility_vertical"], []).append(h / v)
    ratios = {}
    for vertical, values in by_vertical.items():
        if len(values) >= MIN_CALIBRATION_FACILITIES:
            values.sort()
            ratios[vertical] = values[len(values) // 2]
    return ratios


def _vertical_median_cells(rows: list[dict]) -> dict[str, int]:
    by_vertical: dict[str, list[int]] = {}
    for r in rows:
        if r.get("_actual_cells") and r.get("facility_vertical"):
            by_vertical.setdefault(r["facility_vertical"], []).append(r["_actual_cells"])
    medians = {}
    for vertical, values in by_vertical.items():
        if len(values) >= MIN_CALIBRATION_FACILITIES:
            values.sort()
            medians[vertical] = values[len(values) // 2]
    return medians


def build_scores(con: duckdb.DuckDBPyConnection) -> None:
    config = load_scoring_config()
    weights = config["weights"]
    dart_cfg = config["dart"]
    voc_trend_scores = config["voc_trend_scores"]
    bands = config["employee_bands"]
    assumptions = config["assumptions"]
    universe = load_universe()
    vertical_names = {k: v.name for k, v in universe.verticals.items()}
    finishing_ratios = {k: v.finishing_labor_ratio for k, v in universe.verticals.items()}

    df = con.execute("SELECT * FROM facilities").pl()

    df = _percentile_within_group(df, "voc_tons_2020", "facility_vertical", "voc_percentile")
    df = _percentile_within_group(df, "dart_rate", "facility_vertical", "dart_rate_percentile")
    df = df.with_columns(pl.col("dod_awards_here_ttm").log1p().alias("_dod_log"))
    df = _percentile_global(df, "_dod_log", "dod_percentile")

    dart_medians = (
        df.filter(pl.col("dart_rate").is_not_null() & pl.col("facility_vertical").is_not_null())
        .group_by("facility_vertical").agg(pl.col("dart_rate").median().alias("dart_median"))
    )
    df = df.join(dart_medians, on="facility_vertical", how="left")

    dart_trend_map = dart_cfg["trend_scores"]
    df = df.with_columns([
        pl.col("dart_trend").fill_null("no_data").replace_strict(dart_trend_map, default=dart_trend_map["no_data"]).alias("_dart_trend_score"),
        pl.col("voc_trend").fill_null("no_data").replace_strict(voc_trend_scores, default=voc_trend_scores["no_data"]).alias("_voc_trend_score"),
    ])

    sanity_cap = assumptions["employee_count_sanity_cap"]
    magnitude_tiers = config["facility_magnitude_tiers"]
    employees_per_cell = assumptions["employees_per_cell"]
    suspect_count = 0

    # ---- Pass 1: real signals, real cells where an actual OSHA employee_count
    # exists, capped per-facility by magnitude tier (not a single flat cap --
    # see scoring_config.yaml). Nothing in the TCV fallback chain runs yet.
    rows = df.to_dicts()
    for r in rows:
        employee_count = r["employee_count"]
        employee_count_suspect = employee_count is not None and employee_count > sanity_cap
        if employee_count_suspect:
            suspect_count += 1
            employee_count = None
        r["employee_count"] = employee_count
        r["employee_count_suspect"] = employee_count_suspect

        employee_score = _employee_band_score(employee_count, bands)
        magnitude = _facility_magnitude(employee_score, r["voc_percentile"], r["has_air_major_permit"])
        max_cells = _max_cells_for_magnitude(magnitude, magnitude_tiers)
        r["_facility_magnitude"] = round(magnitude, 1)
        r["_max_cells"] = max_cells

        ratio = finishing_ratios.get(r["facility_vertical"], assumptions["default_finishing_labor_ratio"])
        actual_headcount = employee_count * ratio if employee_count is not None else None
        actual_cells = (
            min(max_cells, math.floor(actual_headcount / employees_per_cell))
            if actual_headcount is not None else None
        )
        r["_actual_finishing_headcount"] = actual_headcount
        r["_actual_cells"] = actual_cells

    # ---- Pass 2: calibrate the fallback rungs from Pass-1's real facilities only.
    voc_ratios = _calibration_ratios(rows, "voc_tons_2020")
    pm_ratios = _calibration_ratios(rows, "pm10_tons_2020")
    vertical_median_cells = _vertical_median_cells(rows)

    # ---- Pass 3: score every facility; apply the TCV fallback chain to
    # QUALIFIED facilities that Pass 1 left without a headcount estimate.
    # Non-qualified facilities are never estimated -- there's nothing to
    # guarantee non-zero for.
    scored_rows = []
    fallback_counts = {"actual": 0, "voc_estimated": 0, "pm_estimated": 0, "vertical_median_estimated": 0, "no_estimate": 0, "not_qualified": 0}
    for r in rows:
        voc_score = r["voc_percentile"] or 0.0
        voc_component = voc_score * weights["voc_tonnage_percentile"]
        voc_trend_score = r["_voc_trend_score"]
        voc_trend_component = voc_trend_score * weights["voc_trend_percentile"]
        dart_rate_score = r["dart_rate_percentile"] or 0.0
        dart_trend_score = r["_dart_trend_score"]
        dart_blended_score = dart_rate_score * dart_cfg["rate_sub_weight"] + dart_trend_score * dart_cfg["trend_sub_weight"]
        dart_component = dart_blended_score * weights["dart_rate_and_trend"]
        dod_score = r["dod_percentile"] or 0.0
        dod_component = dod_score * weights["dod_awards_log_scaled"]
        employee_score = _employee_band_score(r["employee_count"], bands)
        employee_component = employee_score * weights["employee_count_band"]
        air_permit_score = 100.0 if r["has_air_major_permit"] else 0.0
        air_permit_component = air_permit_score * weights["air_permit_class"]

        facility_score = (voc_component + voc_trend_component + dart_component
                           + dod_component + employee_component + air_permit_component)

        est_finishing_headcount = r["_actual_finishing_headcount"]
        est_cells_capacity = r["_actual_cells"]
        tcv_basis = "actual" if est_cells_capacity is not None else None

        if not r["qualified_for_tcv"]:
            tcv_basis = "not_qualified"
            est_cells_capacity = 0
        elif est_cells_capacity is None:
            vertical = r["facility_vertical"]
            voc_tons = r["voc_tons_2020"]
            pm_tons = r["pm10_tons_2020"]
            facility_max_cells = r["_max_cells"]
            if vertical and voc_tons and vertical in voc_ratios:
                est_finishing_headcount = voc_tons * voc_ratios[vertical]
                est_cells_capacity = min(facility_max_cells, math.floor(est_finishing_headcount / employees_per_cell))
                tcv_basis = "voc_estimated"
            elif vertical and pm_tons and vertical in pm_ratios:
                est_finishing_headcount = pm_tons * pm_ratios[vertical]
                est_cells_capacity = min(facility_max_cells, math.floor(est_finishing_headcount / employees_per_cell))
                tcv_basis = "pm_estimated"
            elif vertical and vertical in vertical_median_cells:
                est_cells_capacity = min(facility_max_cells, vertical_median_cells[vertical])
                tcv_basis = "vertical_median_estimated"
            else:
                est_cells_capacity = 0
                tcv_basis = "no_estimate"

        fallback_counts[tcv_basis] += 1
        est_facility_tcv = est_cells_capacity * assumptions["per_cell_tcv_usd"]

        r["voc_score"] = round(voc_score, 1)
        r["voc_trend_score"] = round(voc_trend_score, 1)
        r["dart_rate_score"] = round(dart_rate_score, 1)
        r["dart_trend_score"] = round(dart_trend_score, 1)
        r["dod_score"] = round(dod_score, 1)
        r["employee_score"] = round(employee_score, 1)
        r["air_permit_score"] = round(air_permit_score, 1)
        r["voc_component"] = round(voc_component, 2)
        r["voc_trend_component"] = round(voc_trend_component, 2)
        r["dart_component"] = round(dart_component, 2)
        r["dod_component"] = round(dod_component, 2)
        r["employee_component"] = round(employee_component, 2)
        r["air_permit_component"] = round(air_permit_component, 2)
        r["facility_score"] = round(facility_score, 2)
        r["est_finishing_headcount"] = round(est_finishing_headcount, 1) if est_finishing_headcount is not None else None
        r["est_cells_capacity"] = est_cells_capacity
        r["est_facility_tcv"] = float(est_facility_tcv)
        r["tcv_basis"] = tcv_basis
        r["tcv_is_derived"] = tcv_basis not in ("actual", "not_qualified", None)
        # Traceability for the magnitude-tiered cell cap (scoring_config.yaml
        # facility_magnitude_tiers): which tier this facility's cap came
        # from, not just the number itself.
        r["facility_magnitude"] = r["_facility_magnitude"]
        r["facility_cell_cap"] = r["_max_cells"]

        vertical_name = vertical_names.get(r["facility_vertical"])
        r["why_now"] = _why_now(r, vertical_name, r.get("dart_median"))
        scored_rows.append(r)

    drop_cols = ["_dod_log", "_dart_trend_score", "_voc_trend_score", "dart_median",
                 "_actual_finishing_headcount", "_actual_cells", "_facility_magnitude", "_max_cells"]
    scored_df = pl.DataFrame(scored_rows).drop(drop_cols)

    con.register("facilities_scored", scored_df)
    con.execute("CREATE OR REPLACE TABLE facilities AS SELECT * FROM facilities_scored")
    con.unregister("facilities_scored")

    accounts_df = con.execute("SELECT * FROM accounts").pl()
    stale_cols = [c for c in ("est_account_tcv_ceiling",) if c in accounts_df.columns]
    if stale_cols:
        accounts_df = accounts_df.drop(stale_cols)  # re-running score.py against an already-scored accounts table
    tcv_by_account = (
        scored_df.filter(pl.col("qualified_for_tcv") & (pl.col("installed_status") == "untouched"))
        .group_by("account_id").agg(pl.col("est_facility_tcv").sum().alias("est_account_tcv_ceiling"))
    )
    accounts_df = accounts_df.join(tcv_by_account, on="account_id", how="left").with_columns(
        pl.col("est_account_tcv_ceiling").fill_null(0.0)
    )
    con.register("accounts_scored", accounts_df)
    con.execute("CREATE OR REPLACE TABLE accounts AS SELECT * FROM accounts_scored")
    con.unregister("accounts_scored")

    print(f"[score] scored {len(scored_rows):,} facilities")
    if suspect_count:
        print(f"[score] WARNING: {suspect_count} facility record(s) had employee_count > {sanity_cap:,} sanity cap "
              f"-- excluded from headcount/TCV math, flagged employee_count_suspect=true")
    print(f"[score] TCV basis across all facilities: {fallback_counts}")
    total_ceiling = accounts_df["est_account_tcv_ceiling"].sum()
    tier_caps = [t["max_cells"] for t in magnitude_tiers]
    print(f"[score] est_account_TCV_ceiling summed across all accounts: ${total_ceiling:,.0f} "
          f"(per-cell ${assumptions['per_cell_tcv_usd']:,}, magnitude-tiered cap {sorted(tier_caps)} cells/facility)")


def main() -> None:
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        build_scores(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
