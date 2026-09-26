"""Build planner inputs from tests/golden/week/library.json (no database)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.core import library
from mealplan.core.base_week import seed_house_meals
from mealplan.core.components import ComponentSpec, load_components_csv, seed_components
from mealplan.core.normalizer import Catalog
from mealplan.core.parser import parse_ingredient
from mealplan.core.planner import PlanInputs
from mealplan.core.recipe_facts import Dish, DishIngredient, StepTime
from mealplan.ingest import review_queue
from mealplan.models.enums import (
    Collection,
    InventorySource,
    Location,
    Meal,
    MealRole,
    SourceKind,
)
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef, StepDraft
from mealplan.models.tables import Ingredient, InventoryItem, MealSlot

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


def house_meals() -> list[dict[str, Any]]:
    data = json.loads((ROOT / "data" / "house_meals.json").read_text(encoding="utf-8"))
    return [{**r, "role": "dinner"} for r in data["recipes"]]


def golden_inputs(
    catalog: Catalog,
    locked: dict[tuple[date, Meal], str] | None = None,
    drop: set[str] | None = None,
    history: dict[str, date] | None = None,
) -> PlanInputs:
    data = load_golden()
    dishes = tuple(
        dish_from_json(r, catalog)
        for r in [*data["recipes"], *house_meals()]
        if r["ref"] not in (drop or set())
    )
    components: tuple[ComponentSpec, ...] = tuple(
        load_components_csv(ROOT / "data" / "components.csv")
    )
    return PlanInputs(
        dishes=dishes,
        components=components,
        history={
            **{ref: date.fromisoformat(d) for ref, d in data["history"].items()},
            **(history or {}),
        },
        expiring=frozenset(data["expiring"]),
        locked=locked or {},
    )


def golden_start() -> date:
    return date.fromisoformat(load_golden()["week_start"])


def golden_seed() -> int:
    seed: int = load_golden()["seed"]
    return seed


def populate_golden(session: Session, catalog: Catalog) -> None:
    """Import the golden library through the normal library path, with history,
    expiring spinach, and the generic prep components."""
    data = load_golden()
    for raw in data["recipes"]:
        draft = RecipeDraft(
            title=raw["title"],
            servings=raw.get("servings"),
            meal_role=MealRole(raw["role"]),
            tags=raw.get("tags", []),
            collection=Collection(raw.get("collection", "core")),
            ingredients=[IngredientLine(raw_text=line) for line in raw["ingredients"]],
            steps=[
                StepDraft(text=t, active_minutes=a, passive_minutes=p, equipment=e)
                for t, a, p, e in raw["steps"]
            ],
            sources=[SourceRef(kind=SourceKind.MANUAL)],
        )
        recipe = library.create_draft(session, draft, catalog, ref=raw["ref"])
        review_queue.approve(session, recipe)
        if raw.get("family"):
            library.add_to_family(session, recipe, raw["family"])
        for score in raw.get("ratings", []):
            library.rate(session, recipe, score, date(2026, 9, 1))
    for ref, day in data["history"].items():
        recipe = library.get_recipe(session, ref)
        session.add(
            MealSlot(
                date=date.fromisoformat(day), meal=Meal.DINNER, recipe_id=recipe.id, servings=4
            )
        )
    spinach = session.scalars(
        select(Ingredient).where(Ingredient.canonical_name == "spinach")
    ).one()
    session.add(
        InventoryItem(
            ingredient_id=spinach.id,
            qty=5,
            unit="oz",
            location=Location.FRIDGE,
            added_on=date(2026, 10, 1),
            best_by=date(2026, 10, 6),
            source=InventorySource.MANUAL,
        )
    )
    seed_components(session, load_components_csv(ROOT / "data" / "components.csv"))
    seed_house_meals(session, ROOT / "data" / "house_meals.json", catalog)
    session.flush()
