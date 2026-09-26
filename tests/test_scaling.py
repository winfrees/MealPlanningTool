"""REC-6: unit-aware scaling."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mealplan.core.scaling import scale_quantity
from mealplan.core.units import UNITS


@pytest.mark.parametrize(
    ("qty", "unit", "factor", "name", "expected"),
    [
        (1, "tsp", 3, "salt", (1, "tbsp")),
        (1, "tbsp", 0.5, "oil", (1.5, "tsp")),
        (0.5, "cup", 0.5, "flour", (0.25, "cup")),
        (0.25, "cup", 0.5, "flour", (2, "tbsp")),
        (2, "cup", 1.5, "broth", (3, "cup")),
        (1, "cup", 1 / 3, "rice", (1 / 3, "cup")),
        (0.25, "tsp", 0.25, "cayenne", (0.125, "tsp")),
        (3, None, 1 / 3, "eggs", (1, None)),
        (1, None, 1 / 3, "large egg", (1, None)),
        (2, None, 0.5, "onion", (1, None)),
        (1, None, 0.5, "red onion", (0.5, None)),
        (1, "can", 1.5, "chickpeas", (2, "can")),
        (1, "can", 0.25, "chickpeas", (1, "can")),
        (3, "clove", 0.5, "garlic", (2, "clove")),
        (1, "head", 0.5, "cauliflower", (0.5, "head")),
        (500, "g", 1.5, "lamb", (750, "g")),
        (100, "g", 1 / 3, "butter", (33, "g")),
        (1, "kg", 1.5, "chicken", (1.5, "kg")),
        (1, "kg", 0.5, "chicken", (500, "g")),
        (1, "lb", 1.5, "beef", (1.5, "lb")),
        (1, "lb", 1 / 3, "beef", (5.5, "oz")),
        (12, "oz", 2, "pasta", (24, "oz")),
        (2, "dl", 1.5, "milk", (3, "dl")),
        (250, "ml", 0.5, "stock", (125, "ml")),
        (1, "pinch", 2, "saffron", (2, "pinch")),
    ],
)
def test_scale(qty, unit, factor, name, expected):
    got_qty, got_unit = scale_quantity(qty, unit, factor, name)
    assert got_unit == expected[1]
    assert got_qty == pytest.approx(expected[0], abs=1e-6)


def test_factor_must_be_positive():
    with pytest.raises(ValueError):
        scale_quantity(1, "cup", 0)


@given(
    qty=st.floats(min_value=0.01, max_value=5000),
    unit=st.sampled_from([None, *UNITS]),
    factor=st.floats(min_value=0.05, max_value=20),
)
def test_scaled_amount_is_always_positive(qty, unit, factor):
    got, _ = scale_quantity(qty, unit, factor)
    assert got > 0
