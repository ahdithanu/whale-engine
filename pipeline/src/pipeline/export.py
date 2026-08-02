import json

from pipeline.config import DATA_DIR, DUCKDB_PATH

EXPORT_PATH = DATA_DIR / "facilities.json"


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps([]))
    print(f"[export] stub run — wrote {EXPORT_PATH} from {DUCKDB_PATH}")


if __name__ == "__main__":
    main()
