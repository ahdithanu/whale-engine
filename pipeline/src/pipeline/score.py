"""Facility Signal Engine scoring: a 0-100 signal score per resolved facility
from five weighted inputs, plus a deterministic (not LLM-generated) "why now"
string. Weights and dollar/ratio assumptions live in scoring_config.yaml, not
here — see that file for why each knob is what it is.

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
"""

import math
from pathlib import Path

import duckdb
import polars as pl
import yaml

from pipeline.config import DUCKDB_PATH, REPO_ROOT
from pipeline.universe import load_universe

SCORING_CONFIG_PATH = Path(__file__).parent / "scoring_config.yaml"


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
        clauses.append(f"${row['dod_awards_here_ttm']:,.0f} in DoD awards at this location over 24 months")
    if row["est_finishing_headcount"]:
        clauses.append(f"Estimated {row['est_finishing_headcount']:.0f} finishing headcount")
    if not clauses:
        return "No qualifying signal data available for this facility."
    return ". ".join(clauses) + "."


def build_scores(con: duckdb.DuckDBPyConnection) -> None:
    config = load_scoring_config()
    weights = config["weights"]
    dart_cfg = config["dart"]
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

    trend_score_map = dart_cfg["trend_scores"]
    df = df.with_columns([
        pl.col("dart_trend").fill_null("no_data").replace_strict(trend_score_map, default=trend_score_map["no_data"]).alias("_dart_trend_score"),
    ])

    sanity_cap = assumptions["employee_count_sanity_cap"]
    suspect_count = 0

    rows = df.to_dicts()
    scored_rows = []
    for r in rows:
        employee_count = r["employee_count"]
        employee_count_suspect = employee_count is not None and employee_count > sanity_cap
        if employee_count_suspect:
            suspect_count += 1
            employee_count = None  # excluded from every downstream use, not trusted for some and not others

        # Each *_score below is already 0-100; weights are fractions summing
        # to 1.0 (asserted in load_scoring_config), so score * weight lands
        # each component directly on a 0-100 scale and the five sum to a
        # 0-100 facility_score with no further rescaling needed.
        voc_score = r["voc_percentile"] or 0.0
        voc_component = voc_score * weights["voc_tonnage_percentile"]
        dart_rate_score = r["dart_rate_percentile"] or 0.0
        dart_trend_score = r["_dart_trend_score"]
        dart_blended_score = dart_rate_score * dart_cfg["rate_sub_weight"] + dart_trend_score * dart_cfg["trend_sub_weight"]
        dart_component = dart_blended_score * weights["dart_rate_and_trend"]
        dod_score = r["dod_percentile"] or 0.0
        dod_component = dod_score * weights["dod_awards_log_scaled"]
        employee_score = _employee_band_score(employee_count, bands)
        employee_component = employee_score * weights["employee_count_band"]
        air_permit_score = 100.0 if r["has_air_major_permit"] else 0.0
        air_permit_component = air_permit_score * weights["air_permit_class"]

        facility_score = voc_component + dart_component + dod_component + employee_component + air_permit_component

        ratio = finishing_ratios.get(r["facility_vertical"], assumptions["default_finishing_labor_ratio"])
        est_finishing_headcount = employee_count * ratio if employee_count is not None else None
        est_cells_capacity = (
            max(1, math.ceil(est_finishing_headcount / assumptions["employees_per_cell"]))
            if est_finishing_headcount and est_finishing_headcount > 0 else 0
        )
        est_facility_tcv = est_cells_capacity * assumptions["per_cell_tcv_usd"]

        r["employee_count"] = employee_count
        r["employee_count_suspect"] = employee_count_suspect

        r["voc_component"] = round(voc_component, 2)
        r["dart_component"] = round(dart_component, 2)
        r["dod_component"] = round(dod_component, 2)
        r["employee_component"] = round(employee_component, 2)
        r["air_permit_component"] = round(air_permit_component, 2)
        r["facility_score"] = round(facility_score, 2)
        r["est_finishing_headcount"] = round(est_finishing_headcount, 1) if est_finishing_headcount is not None else None
        r["est_cells_capacity"] = est_cells_capacity
        r["est_facility_tcv"] = float(est_facility_tcv)

        vertical_name = vertical_names.get(r["facility_vertical"])
        r["why_now"] = _why_now(r, vertical_name, r.get("dart_median"))
        scored_rows.append(r)

    scored_df = pl.DataFrame(scored_rows).drop(["_dod_log", "_dart_trend_score", "dart_median"])

    con.register("facilities_scored", scored_df)
    con.execute("CREATE OR REPLACE TABLE facilities AS SELECT * FROM facilities_scored")
    con.unregister("facilities_scored")

    accounts_df = con.execute("SELECT * FROM accounts").pl()
    if "est_account_tcv_ceiling" in accounts_df.columns:
        # score.py may be re-run against an already-scored accounts table
        accounts_df = accounts_df.drop("est_account_tcv_ceiling")
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
              f"(likely OSHA filing errors, same class of issue as the earlier scientific-notation total_hours_worked bug) "
              f"-- excluded from headcount/TCV math, flagged employee_count_suspect=true, not silently trusted or dropped")
    total_ceiling = accounts_df["est_account_tcv_ceiling"].sum()
    print(f"[score] est_account_TCV_ceiling summed across all accounts: ${total_ceiling:,.0f} (per-cell assumption: ${assumptions['per_cell_tcv_usd']:,})")


def main() -> None:
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        build_scores(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
