import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "naics_universe.json"


@dataclass(frozen=True)
class Vertical:
    key: str
    name: str
    naics_codes: dict[str, str]
    part_geometry_classes: list[str]
    materials: list[str]
    finishing_labor_note: str
    # Fraction of a facility's total employees estimated to do finishing work
    # (sanding/grinding/blasting/polishing/buffing/inspection/spraying).
    # Illustrative default per vertical, not derived from customer data --
    # see CLAUDE.md's scoring section. A knob to calibrate, not a fact.
    finishing_labor_ratio: float

    @property
    def codes(self) -> list[str]:
        return list(self.naics_codes.keys())


@dataclass(frozen=True)
class AnchorAccount:
    name: str
    vertical: str
    note: str


@dataclass(frozen=True)
class ScopeQuestion:
    key: str
    question: str
    naics_codes: dict[str, str]

    @property
    def codes(self) -> list[str]:
        return list(self.naics_codes.keys())


@dataclass(frozen=True)
class Universe:
    verticals: dict[str, Vertical]
    anchor_accounts: list[AnchorAccount]
    scope_questions: list[ScopeQuestion]


def load_universe(path: Path = CONFIG_PATH) -> Universe:
    raw = json.loads(path.read_text())

    verticals = {
        key: Vertical(
            key=key,
            name=entry["name"],
            naics_codes=entry["naics_codes"],
            part_geometry_classes=entry["part_geometry_classes"],
            materials=entry["materials"],
            finishing_labor_note=entry["finishing_labor_note"],
            finishing_labor_ratio=entry["finishing_labor_ratio"],
        )
        for key, entry in raw["verticals"].items()
    }
    anchor_accounts = [
        AnchorAccount(name=a["name"], vertical=a["vertical"], note=a["note"])
        for a in raw["anchor_accounts"]
    ]
    scope_questions = [
        ScopeQuestion(key=q["key"], question=q["question"], naics_codes=q["naics_codes"])
        for q in raw["scope_questions"]
    ]

    return Universe(verticals=verticals, anchor_accounts=anchor_accounts, scope_questions=scope_questions)


def main() -> None:
    universe = load_universe()
    total = 0
    for vertical in universe.verticals.values():
        count = len(vertical.codes)
        total += count
        print(f"{vertical.name}: {count} NAICS codes")
    print(f"total: {total} NAICS codes across {len(universe.verticals)} verticals")


if __name__ == "__main__":
    main()
