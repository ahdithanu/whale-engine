"""`python -m pipeline.resolve` — full-universe entity resolution.

Not built yet. The real resolver logic lives in pipeline.resolve.anchors,
proven against the three anchor accounts only (Boeing, Oshkosh Corporation,
Caterpillar) per the deliberate scoping decision documented in CLAUDE.md —
run it directly via `uv run python -m pipeline.resolve.report`. Wiring it up
to run against the full NAICS universe is the next step, not this one.
"""

from pipeline.config import DUCKDB_PATH


def main() -> None:
    print(f"[resolve] stub run for the full universe — would read/write entities in {DUCKDB_PATH}")
    print("[resolve] the real anchor-scoped resolver is `uv run python -m pipeline.resolve.report`")


if __name__ == "__main__":
    main()
