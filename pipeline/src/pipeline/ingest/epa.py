"""EPA FRS + NEI facility ingestion, filtered to our target NAICS set.

Source A — EPA Facility Registry Service (FRS), bulk download (not the
get_facilities REST API — its docs don't confirm NAICS filtering, and bulk
avoids undocumented pagination/rate-limit behavior entirely):
https://ordsext.epa.gov/FLA/www3/state_files/national_combined.zip (~1.26GB).

That zip bundles 11 CSVs; we only need two, confirmed by actually opening the
archive rather than guessing historical FRS file layouts:
- NATIONAL_NAICS_FILE.CSV: REGISTRY_ID, PGM_SYS_ACRNM, PGM_SYS_ID,
  INTEREST_TYPE, NAICS_CODE, PRIMARY_INDICATOR, CODE_DESCRIPTION. One row per
  (registry_id, program, naics) — a facility can appear multiple times if
  different EPA programs classified it slightly differently. We match a
  facility if ANY of its rows carries a target NAICS code.
- NATIONAL_FACILITY_FILE.CSV: REGISTRY_ID, PRIMARY_NAME, LOCATION_ADDRESS,
  CITY_NAME, STATE_CODE, POSTAL_CODE, LATITUDE83, LONGITUDE83, and —
  critically — PGM_SYS_ACRNMS, a single column already carrying every program
  system ID attached to the facility as "ACRONYM:ID" pairs (e.g. "AIR:...,
  EIS:12663611, RCRAINFO:..."). This one column is why we don't need the much
  larger NATIONAL_ENVIRONMENTAL_INTEREST_FILE.CSV (1.1GB) or
  NATIONAL_PROGRAM_FILE.CSV (3.0GB) at all — everything we need for "every
  program system ID attached to the facility" is already denormalized here.

Source B — EPA NEI (National Emissions Inventory) facility summaries, bulk:
https://gaftp.epa.gov/air/nei/nei_facility_summaries/{year}_NEI_Facility_summary.zip
2020 alone is COVID-distorted — commercial aerospace was the hardest-hit
vertical in the economy, and VOC tonnage is our highest-weighted scoring
signal with aerospace as our primary vertical. We pull 2017 (last pre-COVID,
pre-tariff-cycle NEI) alongside 2020 so scoring can use both the absolute
2020 level and the 2017-to-2020 trend — a plant with growing VOC output
despite the 2020 dip is a different signal than one that was already flat.
2023 NEI is not yet published (confirmed against EPA's live status page,
still under OAR review) so it isn't an option yet; NEI_YEARS is a list, not a
constant, so adding 2023 later is a one-line change, not a rewrite.

Confirmed pollutant codes for VOC/PM (all reported in TONs, one row per
facility per pollutant per year — no aggregation needed):
  VOC        Volatile Organic Compounds
  PM10-PRI   PM10 Primary (Filterable + Condensable)
  PM25-PRI   PM2.5 Primary (Filterable + Condensable)
We deliberately do NOT sum PM10-PRI and PM25-PRI into one "PM" figure — PM2.5
is a physical subset of PM10, so summing them would double-count and invent a
number that doesn't correspond to anything EPA actually reports.

The join between the two sources is not registry_id-to-registry_id — NEI's
facility key is "eis facility id", not an FRS Registry ID. The real path is:
    NEI.eis_facility_id  ->  FRS.PGM_SYS_ACRNMS contains "EIS:<that id>"  ->  REGISTRY_ID
Facilities with no EIS entry in PGM_SYS_ACRNMS don't join to NEI at all; that
is logged as an unmatched count, not silently dropped.
"""

import re
import zipfile
from pathlib import Path

import duckdb

from pipeline.config import DATA_DIR, EPA_DB_PATH, RAW_DIR
from pipeline.ingest._download import download_cached
from pipeline.universe import load_universe

FRS_URL = "https://ordsext.epa.gov/FLA/www3/state_files/national_combined.zip"
FRS_FILES_NEEDED = ["NATIONAL_FACILITY_FILE.CSV", "NATIONAL_NAICS_FILE.CSV"]

RAW_FRS_DIR = RAW_DIR / "frs"
RAW_NEI_DIR = RAW_DIR / "nei"

NEI_YEARS = [2017, 2020]
NEI_URL_TEMPLATE = "https://gaftp.epa.gov/air/nei/nei_facility_summaries/{year}_NEI_Facility_summary.zip"
NEI_POLLUTANTS = {"VOC": "voc", "PM10-PRI": "pm10", "PM25-PRI": "pm25"}

TREND_UP_THRESHOLD = 0.05   # +5% or more = "up"
TREND_DOWN_THRESHOLD = -0.05  # -5% or more = "down"; between the two = "flat"


def _extract_frs() -> dict[str, Path]:
    zip_path = download_cached(FRS_URL, RAW_FRS_DIR / "national_combined.zip")
    extract_dir = RAW_FRS_DIR / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    paths = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in FRS_FILES_NEEDED:
            dest = extract_dir / name
            if not dest.exists():
                print(f"[ingest.epa] extracting {name} from FRS zip")
                zf.extract(name, extract_dir)
            paths[name] = dest
    return paths


def _extract_nei(year: int) -> Path:
    url = NEI_URL_TEMPLATE.format(year=year)
    zip_path = download_cached(url, RAW_NEI_DIR / f"{year}_NEI_Facility_summary.zip")
    extract_dir = RAW_NEI_DIR / "extracted" / str(year)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise RuntimeError(f"expected exactly one CSV in {zip_path}, found {csv_names}")
        dest = extract_dir / Path(csv_names[0]).name
        if not dest.exists():
            print(f"[ingest.epa] extracting {year} NEI facility summary")
            zf.extract(csv_names[0], extract_dir)
        return extract_dir / csv_names[0]


def _build_frs_facilities(con: duckdb.DuckDBPyConnection, naics_codes: list[str], facility_csv: Path, naics_csv: Path) -> None:
    naics_list_sql = ", ".join(f"'{c}'" for c in naics_codes)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE matched_registry_ids AS
        SELECT
            REGISTRY_ID AS registry_id,
            list(DISTINCT CAST(NAICS_CODE AS VARCHAR)) AS matched_naics_codes
        FROM read_csv(
            '{naics_csv.as_posix()}',
            columns={{'REGISTRY_ID': 'VARCHAR', 'PGM_SYS_ACRNM': 'VARCHAR', 'PGM_SYS_ID': 'VARCHAR',
                      'INTEREST_TYPE': 'VARCHAR', 'NAICS_CODE': 'VARCHAR', 'PRIMARY_INDICATOR': 'VARCHAR',
                      'CODE_DESCRIPTION': 'VARCHAR'}},
            header=true, quote='"', escape='"'
        )
        WHERE CAST(NAICS_CODE AS VARCHAR) IN ({naics_list_sql})
        GROUP BY REGISTRY_ID
    """)

    # Title V major-source proxy: FRS records this directly as INTEREST_TYPE =
    # 'AIR MAJOR' under PGM_SYS_ACRNM = 'AIR' (confirmed against real data —
    # distinct from 'AIR MINOR' / 'AIR SYNTHETIC MINOR'). Title V permits are
    # required for major sources under the Clean Air Act, so this is the
    # closest available signal to "has a Title V permit" without a separate
    # permit-database join we don't have.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE air_major_registry_ids AS
        SELECT DISTINCT REGISTRY_ID AS registry_id
        FROM read_csv(
            '{naics_csv.as_posix()}',
            columns={{'REGISTRY_ID': 'VARCHAR', 'PGM_SYS_ACRNM': 'VARCHAR', 'PGM_SYS_ID': 'VARCHAR',
                      'INTEREST_TYPE': 'VARCHAR', 'NAICS_CODE': 'VARCHAR', 'PRIMARY_INDICATOR': 'VARCHAR',
                      'CODE_DESCRIPTION': 'VARCHAR'}},
            header=true, quote='"', escape='"'
        )
        WHERE PGM_SYS_ACRNM = 'AIR' AND INTEREST_TYPE = 'AIR MAJOR'
    """)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE frs_facilities_raw AS
        SELECT
            f.REGISTRY_ID AS registry_id,
            f.PRIMARY_NAME AS facility_name,
            f.LOCATION_ADDRESS AS address,
            f.CITY_NAME AS city,
            f.STATE_CODE AS state,
            f.POSTAL_CODE AS zip,
            TRY_CAST(f.LATITUDE83 AS DOUBLE) AS latitude,
            TRY_CAST(f.LONGITUDE83 AS DOUBLE) AS longitude,
            f.PGM_SYS_ACRNMS AS program_system_ids_raw,
            m.matched_naics_codes AS matched_naics_codes,
            (am.registry_id IS NOT NULL) AS has_air_major_permit
        FROM read_csv(
            '{facility_csv.as_posix()}',
            columns={{'FRS_FACILITY_DETAIL_REPORT_URL': 'VARCHAR', 'REGISTRY_ID': 'VARCHAR', 'PRIMARY_NAME': 'VARCHAR',
                      'LOCATION_ADDRESS': 'VARCHAR', 'SUPPLEMENTAL_LOCATION': 'VARCHAR', 'CITY_NAME': 'VARCHAR',
                      'COUNTY_NAME': 'VARCHAR', 'FIPS_CODE': 'VARCHAR', 'STATE_CODE': 'VARCHAR', 'STATE_NAME': 'VARCHAR',
                      'COUNTRY_NAME': 'VARCHAR', 'POSTAL_CODE': 'VARCHAR', 'FEDERAL_FACILITY_CODE': 'VARCHAR',
                      'FEDERAL_AGENCY_NAME': 'VARCHAR', 'TRIBAL_LAND_CODE': 'VARCHAR', 'TRIBAL_LAND_NAME': 'VARCHAR',
                      'CONGRESSIONAL_DIST_NUM': 'VARCHAR', 'CENSUS_BLOCK_CODE': 'VARCHAR', 'HUC_CODE': 'VARCHAR',
                      'EPA_REGION_CODE': 'VARCHAR', 'SITE_TYPE_NAME': 'VARCHAR', 'LOCATION_DESCRIPTION': 'VARCHAR',
                      'CREATE_DATE': 'VARCHAR', 'UPDATE_DATE': 'VARCHAR', 'US_MEXICO_BORDER_IND': 'VARCHAR',
                      'PGM_SYS_ACRNMS': 'VARCHAR', 'LATITUDE83': 'VARCHAR', 'LONGITUDE83': 'VARCHAR',
                      'CONVEYOR': 'VARCHAR', 'COLLECT_DESC': 'VARCHAR', 'ACCURACY_VALUE': 'VARCHAR',
                      'REF_POINT_DESC': 'VARCHAR', 'HDATUM_DESC': 'VARCHAR', 'SOURCE_DESC': 'VARCHAR'}},
            header=true, quote='"', escape='"'
        ) f
        JOIN matched_registry_ids m USING (registry_id)
        LEFT JOIN air_major_registry_ids am USING (registry_id)
    """)

    con.execute("""
        CREATE OR REPLACE TEMP TABLE frs_facilities AS
        SELECT
            *,
            regexp_extract(program_system_ids_raw, 'EIS:([0-9]+)', 1) AS eis_facility_id
        FROM frs_facilities_raw
    """)

    n = con.execute("SELECT count(*) FROM frs_facilities").fetchone()[0]
    n_with_eis = con.execute("SELECT count(*) FROM frs_facilities WHERE eis_facility_id != ''").fetchone()[0]
    print(f"[ingest.epa] FRS: {n:,} facilities matched to our NAICS set; {n_with_eis:,} carry an EIS program ID (NEI-joinable)")


def _build_nei_year(con: duckdb.DuckDBPyConnection, year: int, csv_path: Path) -> None:
    pollutant_list_sql = ", ".join(f"'{p}'" for p in NEI_POLLUTANTS)
    pivot_cols = ", ".join(
        f"""max(CASE WHEN "pollutant code" = '{code}' THEN "total emissions" END) AS {suffix}_tons_{year}"""
        for code, suffix in NEI_POLLUTANTS.items()
    )
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE nei_{year} AS
        SELECT
            CAST("eis facility id" AS VARCHAR) AS eis_facility_id,
            {pivot_cols}
        FROM read_csv_auto('{csv_path.as_posix()}', ignore_errors=true)
        WHERE "pollutant code" IN ({pollutant_list_sql})
        GROUP BY "eis facility id"
    """)
    n = con.execute(f"SELECT count(*) FROM nei_{year}").fetchone()[0]
    print(f"[ingest.epa] NEI {year}: {n:,} facilities with VOC/PM10/PM25 data")


def _trend_case(col_2017: str, col_2020: str, alias: str) -> str:
    return f"""
        CASE WHEN {col_2017} IS NULL OR {col_2020} IS NULL THEN NULL
             WHEN {col_2017} = 0 THEN NULL
             ELSE ({col_2020} - {col_2017}) / {col_2017}
        END AS {alias}_pct_change_2017_2020,
        CASE WHEN {col_2017} IS NULL OR {col_2020} IS NULL OR {col_2017} = 0 THEN 'no_data'
             WHEN ({col_2020} - {col_2017}) / {col_2017} >= {TREND_UP_THRESHOLD} THEN 'up'
             WHEN ({col_2020} - {col_2017}) / {col_2017} <= {TREND_DOWN_THRESHOLD} THEN 'down'
             ELSE 'flat'
        END AS {alias}_trend
    """


def build_epa_facilities(con: duckdb.DuckDBPyConnection) -> int:
    universe = load_universe()
    naics_codes = sorted({code for v in universe.verticals.values() for code in v.codes})

    frs_paths = _extract_frs()
    _build_frs_facilities(con, naics_codes, frs_paths["NATIONAL_FACILITY_FILE.CSV"], frs_paths["NATIONAL_NAICS_FILE.CSV"])

    for year in NEI_YEARS:
        nei_csv = _extract_nei(year)
        _build_nei_year(con, year, nei_csv)

    trend_sql = ",\n".join([
        _trend_case("n17.voc_tons_2017", "n20.voc_tons_2020", "voc"),
        _trend_case("n17.pm10_tons_2017", "n20.pm10_tons_2020", "pm10"),
        _trend_case("n17.pm25_tons_2017", "n20.pm25_tons_2020", "pm25"),
    ])

    con.execute(f"""
        CREATE OR REPLACE TABLE epa_facilities AS
        SELECT
            f.registry_id, f.facility_name, f.address, f.city, f.state, f.zip,
            f.latitude, f.longitude, f.matched_naics_codes, f.has_air_major_permit,
            f.program_system_ids_raw, f.eis_facility_id,
            n17.voc_tons_2017, n20.voc_tons_2020,
            n17.pm10_tons_2017, n20.pm10_tons_2020,
            n17.pm25_tons_2017, n20.pm25_tons_2020,
            {trend_sql},
            (n17.eis_facility_id IS NOT NULL) AS matched_to_nei_2017,
            (n20.eis_facility_id IS NOT NULL) AS matched_to_nei_2020
        FROM frs_facilities f
        LEFT JOIN nei_2017 n17 ON f.eis_facility_id = n17.eis_facility_id AND f.eis_facility_id != ''
        LEFT JOIN nei_2020 n20 ON f.eis_facility_id = n20.eis_facility_id AND f.eis_facility_id != ''
    """)

    return con.execute("SELECT count(*) FROM epa_facilities").fetchone()[0]


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(EPA_DB_PATH))
    try:
        n = build_epa_facilities(con)
        n_2020 = con.execute("SELECT count(*) FROM epa_facilities WHERE matched_to_nei_2020").fetchone()[0]
        n_2017 = con.execute("SELECT count(*) FROM epa_facilities WHERE matched_to_nei_2017").fetchone()[0]
        print(f"[ingest.epa] epa_facilities: {n:,} facilities ({n_2020:,} matched to 2020 NEI, {n_2017:,} matched to 2017 NEI)")
    finally:
        con.close()


if __name__ == "__main__":
    main()
