"""Consolidates each source's own DuckDB file into the final DUCKDB_PATH that
resolve/score/export and the resolver read from.

Each ingest module writes to its own file (see config.py) so long-running
ingests don't serialize on DuckDB's single-writer-per-file lock. This is the
step that puts them back together — attach each source file read-only, copy
its table(s) over. Cheap: DuckDB-to-DuckDB copy, no CSV round-trip.
"""

import duckdb

from pipeline.config import DATA_DIR, DOD_DB_PATH, DUCKDB_PATH, EPA_DB_PATH, OSHA_DB_PATH

SOURCES = [
    ("epa", EPA_DB_PATH, ["epa_facilities"]),
    ("osha", OSHA_DB_PATH, ["osha_establishments"]),
    ("dod", DOD_DB_PATH, ["dod_awards"]),
]


def merge() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        for name, db_path, tables in SOURCES:
            if not db_path.exists():
                print(f"[ingest.merge] skipping {name}: {db_path.name} doesn't exist yet (run ingest-{name} first)")
                continue
            con.execute(f"ATTACH '{db_path.as_posix()}' AS {name}_src (READ_ONLY)")
            for table in tables:
                exists = con.execute(
                    f"SELECT count(*) FROM information_schema.tables WHERE table_catalog = '{name}_src' AND table_schema = 'main' AND table_name = '{table}'"
                ).fetchone()[0]
                if not exists:
                    print(f"[ingest.merge] WARNING: {db_path.name} has no table '{table}' — skipping")
                    continue
                con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM {name}_src.{table}")
                n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                print(f"[ingest.merge] {table}: {n:,} rows from {db_path.name}")
            con.execute(f"DETACH {name}_src")
    finally:
        con.close()


if __name__ == "__main__":
    merge()
