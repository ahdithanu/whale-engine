from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

# Final consolidated store — what resolve/score/export and the resolver read.
DUCKDB_PATH = DATA_DIR / "facility_intel.duckdb"

# Each ingest source writes to its OWN DuckDB file. DuckDB only allows one
# writer per file at a time; a shared file meant a long-running ingest (DoD in
# particular, which can run for many minutes working through rate limits)
# held a lock that blocked every other ingest from even starting. Per-source
# files let epa/osha/dod run concurrently; `pipeline.ingest.merge` then copies
# each source's tables into DUCKDB_PATH.
EPA_DB_PATH = DATA_DIR / "epa.duckdb"
OSHA_DB_PATH = DATA_DIR / "osha.duckdb"
DOD_DB_PATH = DATA_DIR / "dod.duckdb"
