"""Units: canonical names, dimension lookup, and conversion via pint plus densities.

Canonical unit names are short kitchen spellings ("tsp", "cup", "g"). Measured units convert
through pint; count units ("can", "clove") convert only to themselves.
"""

import re
from enum import StrEnum
from fractions import Fraction
from functools import cache

import pint


class Dimension(StrEnum):
    MASS = "mass"
    VOLUME = "volume"
    COUNT = "count"


class ConversionError(ValueError):
    pass


# canonical name -> (dimension, pint unit or None for count units)
UNITS: dict[str, tuple[Dimension, str | None]] = {
    "tsp": (Dimension.VOLUME, "teaspoon"),
    "tbsp": (Dimension.VOLUME, "tablespoon"),
    "cup": (Dimension.VOLUME, "cup"),
    "fl oz": (Dimension.VOLUME, "fluid_ounce"),
    "pint": (Dimension.VOLUME, "pint"),
    "quart": (Dimension.VOLUME, "quart"),
    "gallon": (Dimension.VOLUME, "gallon"),
    "ml": (Dimension.VOLUME, "milliliter"),
    "dl": (Dimension.VOLUME, "deciliter"),
    "l": (Dimension.VOLUME, "liter"),
    "g": (Dimension.MASS, "gram"),
    "kg": (Dimension.MASS, "kilogram"),
    "oz": (Dimension.MASS, "ounce"),
    "lb": (Dimension.MASS, "pound"),
    "pinch": (Dimension.COUNT, None),
    "dash": (Dimension.COUNT, None),
    "clove": (Dimension.COUNT, None),
    "can": (Dimension.COUNT, None),
    "jar": (Dimension.COUNT, None),
    "package": (Dimension.COUNT, None),
    "packet": (Dimension.COUNT, None),
    "bag": (Dimension.COUNT, None),
    "box": (Dimension.COUNT, None),
    "bottle": (Dimension.COUNT, None),
    "bunch": (Dimension.COUNT, None),
    "head": (Dimension.COUNT, None),
    "stalk": (Dimension.COUNT, None),
    "sprig": (Dimension.COUNT, None),
    "slice": (Dimension.COUNT, None),
    "stick": (Dimension.COUNT, None),
    "piece": (Dimension.COUNT, None),
    "handful": (Dimension.COUNT, None),
    "fillet": (Dimension.COUNT, None),
    "sheet": (Dimension.COUNT, None),
    "cube": (Dimension.COUNT, None),
    "ear": (Dimension.COUNT, None),
    "leaf": (Dimension.COUNT, None),
    "scoop": (Dimension.COUNT, None),
}

_ALIASES: dict[str, str] = {
    "t": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tsps": "tsp",
    "T": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tbs": "tbsp",
    "tbl": "tbsp",
    "tbsps": "tbsp",
    "c": "cup",
    "cups": "cup",
    "fluid ounce": "fl oz",
    "fluid ounces": "fl oz",
    "fl. oz": "fl oz",
    "floz": "fl oz",
    "pints": "pint",
    "pt": "pint",
    "quarts": "quart",
    "qt": "quart",
    "gallons": "gallon",
    "gal": "gallon",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "mL": "ml",
    "deciliter": "dl",
    "deciliters": "dl",
    "decilitre": "dl",
    "decilitres": "dl",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "L": "l",
    "gram": "g",
    "grams": "g",
    "gr": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "ounce": "oz",
    "ounces": "oz",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "pinches": "pinch",
    "dashes": "dash",
    "cloves": "clove",
    "cans": "can",
    "tin": "can",
    "tins": "can",
    "jars": "jar",
    "packages": "package",
    "pkg": "package",
    "pkgs": "package",
    "packets": "packet",
    "envelope": "packet",
    "envelopes": "packet",
    "bags": "bag",
    "boxes": "box",
    "bottles": "bottle",
    "bunches": "bunch",
    "heads": "head",
    "stalks": "stalk",
    "sprigs": "sprig",
    "slices": "slice",
    "sticks": "stick",
    "pieces": "piece",
    "handfuls": "handful",
    "fillets": "fillet",
    "sheets": "sheet",
    "cubes": "cube",
    "ears": "ear",
    "leaves": "leaf",
    "scoops": "scoop",
}


def canonical_unit(token: str) -> str | None:
    """Map a unit token as written ("Tbsp.", "cups", "T") to its canonical name, or None."""
    t = token.strip().rstrip(".")
    if t in ("T", "L", "mL"):  # case matters: T is tablespoon, t is teaspoon
        return _ALIASES[t]
    t = re.sub(r"\s+", " ", t.lower())
    if t in UNITS:
        return t
    return _ALIASES.get(t)


def dimension(unit: str) -> Dimension:
    try:
        return UNITS[unit][0]
    except KeyError:
        raise ConversionError(f"unknown unit {unit!r}") from None


@cache
def _registry() -> pint.UnitRegistry:
    return pint.UnitRegistry()


def convert(
    qty: float, from_unit: str, to_unit: str, density_g_per_ml: float | None = None
) -> float:
    """Convert between canonical units; volume <-> mass needs a density."""
    if from_unit == to_unit:
        return qty
    src_dim, src_pint = UNITS.get(from_unit, (None, None))
    dst_dim, dst_pint = UNITS.get(to_unit, (None, None))
    if src_pint is None or dst_pint is None:
        raise ConversionError(f"cannot convert {from_unit!r} to {to_unit!r}")
    ureg = _registry()
    q = qty * ureg(src_pint)
    if src_dim != dst_dim:
        if density_g_per_ml is None:
            raise ConversionError(f"{from_unit!r} to {to_unit!r} needs a density")
        if src_dim is Dimension.VOLUME:
            q = q.to("milliliter").magnitude * density_g_per_ml * ureg.gram
        else:
            q = q.to("gram").magnitude / density_g_per_ml * ureg.milliliter
    return float(q.to(dst_pint).magnitude)


_NICE_DENOMINATORS = (2, 3, 4, 8)


def format_qty(qty: float) -> str:
    """Render 1.5 as "1 1/2" and 0.333 as "1/3"; falls back to up to two decimals."""
    whole = int(qty)
    frac = qty - whole
    if frac < 1e-6:
        return str(whole)
    for d in _NICE_DENOMINATORS:
        n = round(frac * d)
        if 0 < n < d and abs(frac - n / d) < 0.01:
            f = Fraction(n, d)
            return f"{whole} {f}" if whole else str(f)
    return f"{qty:.2f}".rstrip("0").rstrip(".")


EACH = "each"  # the unit of a plain count ("3 onions")


def try_convert(
    qty: float, from_unit: str | None, to_unit: str | None, density_g_per_ml: float | None = None
) -> float | None:
    """Like `convert`, but None when the units cannot be compared. A missing unit is a
    plain count, so it only matches another plain count."""
    src = from_unit or EACH
    dst = to_unit or EACH
    if src == dst:
        return qty
    if EACH in (src, dst):
        return None
    try:
        return convert(qty, src, dst, density_g_per_ml)
    except ConversionError:
        return None
