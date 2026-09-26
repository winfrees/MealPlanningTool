"""REC-6: scale a quantity with unit-aware rounding (no "0.33 eggs", no "0.19 cup").

Scaled amounts are re-expressed in the friendliest unit of the same system (tsp/tbsp/cup,
g/kg, oz/lb) and rounded to amounts a cook can measure.
"""

import math
import re

from mealplan.core.units import UNITS, Dimension, convert

_CUP_FRACTIONS = (0.0, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 3 / 4, 1.0)
_WHOLE_ONLY_NAMES = re.compile(r"\beggs?\b|\byolks?\b|\bwhites?\b", re.IGNORECASE)
_HALVES_OK_UNITS = {"head", "bunch", "stick", "piece", "fillet"}


def _round_to(x: float, step: float) -> float:
    return math.floor(x / step + 0.5) * step


def _round_cups(cups: float) -> float:
    whole = math.floor(cups)
    frac = min(_CUP_FRACTIONS, key=lambda f: abs(cups - whole - f))
    return whole + frac


def _spoon_or_cup(qty: float, unit: str) -> tuple[float, str]:
    tsp = convert(qty, unit, "tsp")
    if tsp >= 12 - 1e-9:  # 1/4 cup and up
        return _round_cups(tsp / 48), "cup"
    if tsp >= 3 - 1e-9:
        return _round_to(tsp / 3, 0.5), "tbsp"
    return max(_round_to(tsp, 0.125), 0.125), "tsp"


def _count(qty: float, unit: str | None, name: str) -> float:
    if unit in _HALVES_OK_UNITS or (unit is None and not _WHOLE_ONLY_NAMES.search(name)):
        return max(_round_to(qty, 0.5), 0.5)
    return max(float(math.floor(qty + 0.5)), 1.0)


def scale_quantity(
    qty: float, unit: str | None, factor: float, name: str = ""
) -> tuple[float, str | None]:
    """Scale ``qty unit`` by ``factor`` and round it to a measurable amount."""
    if factor <= 0:
        raise ValueError("scale factor must be positive")
    scaled = qty * factor
    if unit is None or UNITS[unit][0] is Dimension.COUNT:
        return _count(scaled, unit, name), unit

    if unit in ("tsp", "tbsp", "cup"):
        return _spoon_or_cup(scaled, unit)
    if unit in ("fl oz", "pint", "quart", "gallon"):
        return _round_to(scaled, 0.25) or 0.25, unit
    if unit in ("ml", "dl", "l"):
        ml = convert(scaled, unit, "ml")
        if unit == "ml" or ml < 100:
            return max(_round_to(ml, 5), 5), "ml"
        return _round_to(scaled, 0.05 if unit == "l" else 0.5), unit
    if unit in ("g", "kg"):
        grams = convert(scaled, unit, "g")
        if unit == "g" or grams < 1000:
            return max(round(grams) if grams < 50 else _round_to(grams, 5), 1), "g"
        return _round_to(scaled, 0.05), "kg"
    # oz / lb
    oz = convert(scaled, unit, "oz")
    if unit == "oz" or oz < 8:
        return max(_round_to(oz, 0.5), 0.5), "oz"
    return _round_to(scaled, 0.25), "lb"
