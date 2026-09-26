import pytest

from mealplan.core.units import (
    ConversionError,
    Dimension,
    canonical_unit,
    convert,
    dimension,
    format_qty,
)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("Tbsp.", "tbsp"),
        ("T", "tbsp"),
        ("t", "tsp"),
        ("cups", "cup"),
        ("fluid ounces", "fl oz"),
        ("lbs", "lb"),
        ("mL", "ml"),
        ("tins", "can"),
        ("dl", "dl"),
        ("tomatoes", None),
    ],
)
def test_canonical_unit(token, expected):
    assert canonical_unit(token) == expected


def test_convert_same_dimension():
    assert convert(1, "cup", "tbsp") == pytest.approx(16)
    assert convert(1, "lb", "oz") == pytest.approx(16)
    assert convert(2, "dl", "ml") == pytest.approx(200)


def test_convert_volume_to_mass_needs_density():
    with pytest.raises(ConversionError, match="density"):
        convert(1, "cup", "g")
    # all-purpose flour ~0.53 g/ml -> about 125 g per cup
    assert convert(1, "cup", "g", density_g_per_ml=0.53) == pytest.approx(125.4, abs=0.1)
    assert convert(125.4, "g", "cup", density_g_per_ml=0.53) == pytest.approx(1, abs=0.01)


def test_count_units_do_not_convert():
    assert convert(2, "can", "can") == 2
    with pytest.raises(ConversionError):
        convert(1, "can", "oz")
    assert dimension("clove") is Dimension.COUNT
    with pytest.raises(ConversionError):
        dimension("furlong")


@pytest.mark.parametrize(
    ("qty", "text"),
    [(1, "1"), (1.5, "1 1/2"), (1 / 3, "1/3"), (0.125, "1/8"), (2.75, "2 3/4"), (0.3, "0.3")],
)
def test_format_qty(qty, text):
    assert format_qty(qty) == text
