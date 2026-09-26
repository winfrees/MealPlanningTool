"""Build planner inputs from tests/golden/week/library.json (no database)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from mealplan.core.components import ComponentSpec, load_components_csv
from mealplan.core.normalizer import Catalog
from mealplan.core.parser import parse_ingredient
from mealplan.core.planner import PlanInputs
from mealplan.core.recipe_facts import Dish, DishIngredient, StepTime
from mealplan.models.enums import Collection, Meal, MealRole

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_WEEK = ROOT / "tests" / "golden" / "week"


def load_golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((GOLDEN_WEEK / "library.json").read_text(encoding="utf-8"))
    return data


def _favorite(ratings: list[int]) -> float | None:
    if not ratings:
        return None
    # Mirrors library.favorite_score for ratings without would_repeat.
    return round(sum(ratings) / len(ratings) + 0.1 * min(len(ratings), 5), 3)


def dish_from_json(raw: dict[str, Any], catalog: Catalog) -> Dish:
    ingredients = []
    for line in raw.get("ingredients", []):
        parsed = parse_ingredient(line)
        match = catalog.match(parsed.name)
        name = match.entry.canonical_name if match else parsed.name
        ingredients.append(DishIngredient(name, parsed.qty, parsed.unit))
    return Dish(
        ref=raw["ref"],
        title=raw["title"],
        role=MealRole(raw["role"]),
        servings=raw.get("servings"),
        collection=Collection(raw.get("collection", "core")),
        family=raw.get("family"),
        tags=frozenset(raw.get("tags", [])),
        ingredients=tuple(ingredients),
        steps=tuple(StepTime(t, a, p, tuple(e)) for t, a, p, e in raw.get("steps", [])),
        prep_minutes=raw.get("prep_minutes"),
        cook_minutes=raw.get("cook_minutes"),
        favorite=_favorite(raw.get("ratings", [])),
    )


def golden_inputs(
    catalog: Catalog,
    locked: dict[tuple[date, Meal], str] | None = None,
    drop: set[str] | None = None,
) -> PlanInputs:
    data = load_golden()
    dishes = tuple(
        dish_from_json(r, catalog) for r in data["recipes"] if r["ref"] not in (drop or set())
    )
    components: tuple[ComponentSpec, ...] = tuple(
        load_components_csv(ROOT / "data" / "components.csv")
    )
    return PlanInputs(
        dishes=dishes,
        components=components,
        history={ref: date.fromisoformat(d) for ref, d in data["history"].items()},
        expiring=frozenset(data["expiring"]),
        locked=locked or {},
    )


def golden_start() -> date:
    return date.fromisoformat(load_golden()["week_start"])


def golden_seed() -> int:
    seed: int = load_golden()["seed"]
    return seed
