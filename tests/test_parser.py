"""REC-2: golden ingredient lines (tests/golden/ingredient_lines.json)."""

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mealplan.core.parser import parse_ingredient

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "ingredient_lines.json").read_text(encoding="utf-8")
)
FIELDS = ("qty", "qty_max", "unit", "size_qty", "size_unit", "name", "prep_note", "optional")
DEFAULTS = {"prep_note": "", "optional": False}


@pytest.mark.parametrize("case", GOLDEN, ids=[c["raw"] for c in GOLDEN])
def test_golden_line(case):
    parsed = parse_ingredient(case["raw"])
    assert parsed.raw == case["raw"]
    for field in FIELDS:
        expected = case.get(field, DEFAULTS.get(field))
        actual = getattr(parsed, field)
        if isinstance(expected, float):
            assert actual == pytest.approx(expected, abs=1e-3), field
        else:
            assert actual == expected, field


@given(st.text(max_size=80))
def test_never_raises_and_keeps_raw(line):
    parsed = parse_ingredient(line)
    assert parsed.raw == line
    assert parsed.qty is None or parsed.qty >= 0
