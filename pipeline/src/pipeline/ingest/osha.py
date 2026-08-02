"""OSHA ITA (Injury Tracking Application) establishment-level ingestion.

Source: OSHA Form 300A Summary Data, bulk CSV at
osha.gov/Establishment-Specific-Injury-and-Illness-Data. Schema verified
directly against the downloaded files (stable across 2023-2025):
id, establishment_name, establishment_id, ein, company_name, street_address,
city, state, zip_code, naics_code, naics_year, industry_description,
establishment_type, size, annual_average_employees, total_hours_worked,
no_injuries_illnesses, total_deaths, total_dafw_cases, total_djtr_cases,
total_other_cases, total_dafw_days, total_djtr_days, total_injuries,
total_skin_disorders, total_respiratory_conditions, total_poisonings,
total_hearing_loss, total_other_illnesses, created_timestamp, change_reason,
year_filing_for.

DART (Days Away, Restricted, or Transferred) rate = (DART cases x 200,000) /
total hours worked — OSHA's standard incidence-rate formula, where 200,000 is
100 full-time workers x 2,000 hours/year. DART cases = DAFW + DJTR cases.
TRC (Total Recordable Case) rate adds "other recordable" cases on top of DART.
"""

import zipfile
from pathlib import Path

import duckdb
import polars as pl

from pipeline.config import DATA_DIR, OSHA_DB_PATH, RAW_DIR
from pipeline.ingest._download import download_cached
from pipeline.universe import load_universe

OSHA_YEARS = {
    2025: "https://www.osha.gov/sites/default/files/ITA_300A_Summary_Data_2025_through_03-15-2026_v2.csv",
    2024: "https://www.osha.gov/sites/default/files/ITA_300A_Summary_Data_2024_through_12-31-2025.zip",
    2023: "https://www.osha.gov/sites/default/files/ITA_300A_Summary_Data_2023_through_12-31-2024.zip",
}

RAW_OSHA_DIR = RAW_DIR / "osha"

SCHEMA_OVERRIDES = {
    "naics_code": pl.Utf8,
    "zip_code": pl.Utf8,
    "establishment_id": pl.Utf8,
    "ein": pl.Utf8,
    # Float, not Int: the raw CSVs contain scientific-notation and other
    # malformed numeric entries (e.g. total_hours_worked = "1.40142E+11" for
    # at least one establishment) that fail integer parsing. These are almost
    # certainly filer data-entry errors, not real values — we keep them as-is
    # rather than silently "fixing" someone's regulatory filing, but downstream
    # DART/TRC rate math should treat implausible hours-worked as a data
    # quality flag, not gospel.
    "annual_average_employees": pl.Float64,
    "total_hours_worked": pl.Float64,
    "no_injuries_illnesses": pl.Float64,
    "total_deaths": pl.Float64,
    "total_dafw_cases": pl.Float64,
    "total_djtr_cases": pl.Float64,
    "total_other_cases": pl.Float64,
    "total_dafw_days": pl.Float64,
    "total_djtr_days": pl.Float64,
    "total_injuries": pl.Float64,
    "total_skin_disorders": pl.Float64,
    "total_respiratory_conditions": pl.Float64,
    "total_poisonings": pl.Float64,
    "total_hearing_loss": pl.Float64,
    "total_other_illnesses": pl.Float64,
}

# Column order in the raw CSV differs slightly across years (2024 swaps
# change_reason/created_timestamp relative to 2023 and 2025); select() by name
# to a fixed order before concatenating so schemas line up regardless.
RAW_COLUMNS = [
    "id", "establishment_name", "establishment_id", "ein", "company_name",
    "street_address", "city", "state", "zip_code", "naics_code", "naics_year",
    "industry_description", "establishment_type", "size",
    "annual_average_employees", "total_hours_worked", "no_injuries_illnesses",
    "total_deaths", "total_dafw_cases", "total_djtr_cases", "total_other_cases",
    "total_dafw_days", "total_djtr_days", "total_injuries",
    "total_skin_disorders", "total_respiratory_conditions", "total_poisonings",
    "total_hearing_loss", "total_other_illnesses", "created_timestamp",
    "change_reason", "year_filing_for",
]

OUTPUT_COLUMNS = [
    "establishment_id", "year_filing_for", "establishment_name", "company_name",
    "street_address", "city", "state", "zip_code",
    "naics_code", "naics_year", "industry_description",
    "annual_average_employees", "total_hours_worked",
    "total_dafw_cases", "total_djtr_cases", "total_other_cases",
    "dart_cases", "dart_rate", "total_recordable_cases", "trc_rate",
    "total_deaths", "total_injuries",
]


def _fetch_year_csv(year: int, url: str) -> Path:
    dest = RAW_OSHA_DIR / Path(url).name
    download_cached(url, dest)
    if dest.suffix == ".zip":
        with zipfile.ZipFile(dest) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise RuntimeError(f"expected exactly one CSV in {dest}, found {names}")
            extract_dir = RAW_OSHA_DIR / str(year)
            zf.extract(names[0], extract_dir)
            return extract_dir / names[0]
    return dest


def _load_year(year: int, url: str, naics_codes: set[str]) -> pl.DataFrame:
    csv_path = _fetch_year_csv(year, url)
    # ignore_errors: the raw filings contain occasional malformed rows (e.g. a
    # timestamp value landing in a numeric injury-count column, presumably a
    # source-side export/quoting defect on OSHA's end). Those cells become
    # null rather than crashing the whole year's load; we log how many so it's
    # auditable, not silently swallowed.
    df = pl.read_csv(
        csv_path,
        infer_schema_length=10000,
        schema_overrides=SCHEMA_OVERRIDES,
        ignore_errors=True,
    ).select(RAW_COLUMNS)
    df = df.filter(pl.col("naics_code").is_in(naics_codes))
    df = df.filter(pl.col("year_filing_for").cast(pl.Int64) == year)
    null_hours = df.select(pl.col("total_hours_worked").is_null().sum()).item()
    if null_hours:
        print(f"[ingest.osha] year {year}: {null_hours} of {df.height} NAICS-matched rows have unparseable total_hours_worked (nulled, excluded from rate calc)")
    return df


def build_osha_establishments(con: duckdb.DuckDBPyConnection) -> int:
    universe = load_universe()
    naics_codes = {code for v in universe.verticals.values() for code in v.codes}

    frames = [_load_year(year, url, naics_codes) for year, url in OSHA_YEARS.items()]
    combined = pl.concat(frames, how="vertical_relaxed")

    dart_cases = pl.col("total_dafw_cases") + pl.col("total_djtr_cases")
    trc_cases = dart_cases + pl.col("total_other_cases")

    combined = combined.with_columns([
        dart_cases.alias("dart_cases"),
        trc_cases.alias("total_recordable_cases"),
        pl.when(pl.col("total_hours_worked") > 0)
          .then(dart_cases * 200_000 / pl.col("total_hours_worked"))
          .otherwise(None)
          .alias("dart_rate"),
        pl.when(pl.col("total_hours_worked") > 0)
          .then(trc_cases * 200_000 / pl.col("total_hours_worked"))
          .otherwise(None)
          .alias("trc_rate"),
    ])

    out = combined.select(OUTPUT_COLUMNS).rename({"year_filing_for": "year"})

    con.register("osha_out", out)
    con.execute("CREATE OR REPLACE TABLE osha_establishments AS SELECT * FROM osha_out")
    con.unregister("osha_out")
    return out.height


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(OSHA_DB_PATH))
    try:
        n = build_osha_establishments(con)
        print(f"[ingest.osha] osha_establishments: {n:,} rows across years {sorted(OSHA_YEARS)}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
