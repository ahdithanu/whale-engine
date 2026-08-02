.PHONY: setup ingest ingest-epa ingest-osha ingest-dod merge resolve score export dev

setup:
	cd pipeline && uv sync
	cd web && npm install

# epa/osha/dod each write to their own DuckDB file and can run concurrently
# (e.g. `make ingest-epa & make ingest-osha & make ingest-dod & wait`);
# merge is what consolidates them into facility_intel.duckdb afterward.
ingest: ingest-epa ingest-osha ingest-dod merge

ingest-epa:
	cd pipeline && uv run python -m pipeline.ingest.epa

ingest-osha:
	cd pipeline && uv run python -m pipeline.ingest.osha

ingest-dod:
	cd pipeline && uv run python -m pipeline.ingest.dod

merge:
	cd pipeline && uv run python -m pipeline.ingest.merge

resolve:
	cd pipeline && uv run python -m pipeline.resolve

score:
	cd pipeline && uv run python -m pipeline.score

export:
	cd pipeline && uv run python -m pipeline.export

dev:
	cd web && npm run dev
