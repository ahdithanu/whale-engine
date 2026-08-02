import re

from pipeline.universe import load_universe

NAICS_RE = re.compile(r"^\d{6}$")


def test_universe_loads():
    universe = load_universe()
    assert len(universe.verticals) > 0


def test_every_naics_code_is_six_digits():
    universe = load_universe()
    for vertical in universe.verticals.values():
        for code in vertical.codes:
            assert NAICS_RE.match(code), f"{code!r} in {vertical.name!r} is not 6 digits"
    for question in universe.scope_questions:
        for code in question.codes:
            assert NAICS_RE.match(code), f"{code!r} in scope question {question.key!r} is not 6 digits"


def test_every_vertical_has_ui_metadata():
    universe = load_universe()
    for vertical in universe.verticals.values():
        assert vertical.part_geometry_classes
        assert vertical.materials
        assert vertical.finishing_labor_note


def test_anchor_accounts_reference_known_verticals():
    universe = load_universe()
    assert {a.name for a in universe.anchor_accounts} == {"Boeing", "Oshkosh Corporation", "Caterpillar"}
    for account in universe.anchor_accounts:
        assert account.vertical in universe.verticals


def test_scope_questions_are_flagged_not_included_in_verticals():
    universe = load_universe()
    assert len(universe.scope_questions) > 0
    for question in universe.scope_questions:
        assert question.question
