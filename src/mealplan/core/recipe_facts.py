"""Planning facts derived from a recipe: protein, hands-on time, spice, season, equipment.

Pure functions over `Dish`, a plain snapshot of a recipe, so the planner never touches the
database. Every derived fact can be overridden by a tag, so the reviewer has the last word:
`protein:<name>`, `weeknight-friendly`, `mild` / `very-spicy`, `no-leftovers`.
"""

import re
from dataclasses import dataclass
from datetime import date

from mealplan.core.units import UNITS, ConversionError, Dimension, convert
from mealplan.models.enums import Collection, MealRole


@dataclass(frozen=True)
class StepTime:
    text: str
    active_minutes: int = 0
    passive_minutes: int = 0
    equipment: tuple[str, ...] = ()


@dataclass(frozen=True)
class DishIngredient:
    name: str  # catalog canonical name, or the raw line when unmatched
    qty: float | None = None
    unit: str | None = None
    optional: bool = False
    matched: bool = True  # False when `name` is a raw line the catalog did not match


@dataclass(frozen=True)
class Dish:
    ref: str
    title: str
    role: MealRole | None
    servings: float | None = None
    collection: Collection = Collection.CORE
    family: str | None = None
    family_preferred: bool = False  # pinned as the family's preferred variant
    tags: frozenset[str] = frozenset()
    ingredients: tuple[DishIngredient, ...] = ()
    steps: tuple[StepTime, ...] = ()
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    favorite: float | None = None  # library.favorite_score; None when unrated


# --- protein --------------------------------------------------------------------------------

_PROTEIN_WORDS: tuple[tuple[str, str], ...] = (
    ("shrimp", "shellfish"),
    ("prawn", "shellfish"),
    ("salmon", "fish"),
    ("cod", "fish"),
    ("snapper", "fish"),
    ("tuna", "fish"),
    ("fish", "fish"),
    ("chicken", "chicken"),
    ("turkey", "turkey"),
    ("lamb", "lamb"),
    ("beef", "beef"),
    ("steak", "beef"),
    ("chuck", "beef"),
    ("pork", "pork"),
    ("bacon", "pork"),
    ("sausage", "pork"),
    ("ham", "pork"),
    ("pancetta", "pork"),
    ("chorizo", "pork"),
    ("salami", "pork"),
    ("prosciutto", "pork"),
)
_NOT_PROTEIN = ("broth", "stock", "bouillon", "fish sauce", "oyster sauce")


def protein(dish: Dish) -> str:
    """The main protein: first meat or seafood ingredient in list order, else vegetarian."""
    for tag in dish.tags:
        if tag.startswith("protein:"):
            return tag.split(":", 1)[1]
    for ing in dish.ingredients:
        name = ing.name.lower()
        if any(word in name for word in _NOT_PROTEIN):
            continue
        for word, kind in _PROTEIN_WORDS:
            if re.search(rf"\b{word}", name):
                return kind
    return "vegetarian"


# --- time and equipment ---------------------------------------------------------------------


def active_minutes(dish: Dish) -> int | None:
    """Hands-on minutes: step times if recorded, else the stated prep time, else unknown."""
    steps = sum(s.active_minutes for s in dish.steps)
    if steps:
        return steps
    return dish.prep_minutes


def passive_minutes(dish: Dish) -> int:
    steps = sum(s.passive_minutes for s in dish.steps)
    return steps or (dish.cook_minutes or 0)


_EQUIPMENT_WORDS = (
    ("slow cooker", r"slow cooker|crock ?pot"),
    ("pressure cooker", r"instant pot|pressure cook"),
    ("oven", r"\boven\b|\bbake|\broast|\bbroil"),
    ("stove", r"simmer|boil|saut[eé]|skillet|saucepan|\bpot\b|\bpan\b|fry|stir"),
)


def equipment(dish: Dish) -> tuple[str, ...]:
    """Equipment from step data, or inferred from step text when none was recorded."""
    found: set[str] = {e for s in dish.steps for e in s.equipment}
    if not found:
        text = " ".join(s.text.lower() for s in dish.steps)
        found = {name for name, pattern in _EQUIPMENT_WORDS if re.search(pattern, text)}
    return tuple(sorted(found))


# --- spice ----------------------------------------------------------------------------------

# Heat per ingredient, and the per-serving amount that makes it one level hotter.
_HEAT: dict[str, tuple[int, float, str | None]] = {
    "mitmita": (3, 0.25, "tsp"),
    "thai chile": (2, 0.5, None),
    "serrano pepper": (2, 0.5, None),
    "cayenne pepper": (2, 0.25, "tsp"),
    "berbere": (2, 1.5, "tsp"),
    "chipotle in adobo": (1, 0.5, None),
    "jalapeño": (1, 0.5, None),
    "red pepper flakes": (1, 0.5, "tsp"),
    "sriracha": (1, 1.0, "tsp"),
    "harissa": (1, 1.0, "tsp"),
    "hot sauce": (1, 1.0, "tsp"),
    "thai red curry paste": (1, 1.5, "tsp"),
    "thai green curry paste": (1, 1.5, "tsp"),
}


def _per_serving(ing: DishIngredient, unit: str | None, servings: float) -> float | None:
    if ing.qty is None:
        return None
    qty = ing.qty
    if unit is not None:
        if ing.unit is None or UNITS.get(ing.unit, (Dimension.COUNT, None))[0] is Dimension.COUNT:
            return None
        try:
            qty = convert(ing.qty, ing.unit, unit)
        except ConversionError:
            return None
    elif ing.unit is not None and UNITS[ing.unit][0] is not Dimension.COUNT:
        return None
    return qty / servings


def spice_level(dish: Dish) -> int:
    """0 mild to 3 very spicy. Tags `mild` and `very-spicy` override the estimate."""
    if "very-spicy" in dish.tags:
        return 3
    if "mild" in dish.tags:
        return 0
    servings = dish.servings or 4
    level = 0
    for ing in dish.ingredients:
        heat = _HEAT.get(ing.name)
        if heat is None:
            continue
        base, hot_amount, unit = heat
        amount = _per_serving(ing, unit, servings)
        level = max(level, base + (1 if amount is not None and amount >= hot_amount else 0))
    return min(level, 3)


# --- season ---------------------------------------------------------------------------------

_SEASONS: dict[str, frozenset[int]] = {
    "butternut squash": frozenset({9, 10, 11, 12, 1, 2}),
    "sweet potato": frozenset({10, 11, 12, 1, 2}),
    "brussels sprouts": frozenset({10, 11, 12, 1}),
    "kale": frozenset({10, 11, 12, 1, 2, 3}),
    "rhubarb": frozenset({4, 5, 6}),
    "asparagus": frozenset({3, 4, 5, 6}),
    "strawberry": frozenset({5, 6, 7}),
    "peach": frozenset({7, 8, 9}),
    "zucchini": frozenset({6, 7, 8, 9}),
    "tomato": frozenset({7, 8, 9}),
    "corn": frozenset({7, 8, 9}),
}


def season_fit(dish: Dish, on: date) -> float:
    """+1 per seasonal ingredient in season, -1 per one out of season, clipped to [-1, 1]."""
    score = 0
    for ing in dish.ingredients:
        months = _SEASONS.get(ing.name)
        if months is not None:
            score += 1 if on.month in months else -1
    return max(-1.0, min(1.0, float(score)))


def leftover_friendly(dish: Dish, excluded_proteins: list[str]) -> bool:
    return "no-leftovers" not in dish.tags and protein(dish) not in excluded_proteins
