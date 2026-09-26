"""Planning facts (PLN-1 inputs): protein, time, equipment, spice, season."""

from datetime import date
from typing import Any

import pytest

from mealplan.core import recipe_facts as f
from mealplan.core.recipe_facts import Dish, DishIngredient, StepTime
from mealplan.models.enums import MealRole


def dish(*ings: tuple[str, float | None, str | None], **kw: Any) -> Dish:
    base: dict[str, Any] = {"ref": "x", "title": "X", "role": MealRole.DINNER, "servings": 4}
    base.update(kw)
    return Dish(ingredients=tuple(DishIngredient(*i) for i in ings), **base)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["onion", "chicken thigh", "ground beef"], "chicken"),
        (["chicken broth", "red lentils"], "vegetarian"),
        (["fish sauce", "shrimp"], "shellfish"),
        (["cod"], "fish"),
        (["italian sausage", "kale"], "pork"),
        (["egg", "spinach"], "vegetarian"),
        (["ground lamb"], "lamb"),
    ],
)
def test_protein(names, expected):
    assert f.protein(dish(*[(n, 1, None) for n in names])) == expected


def test_protein_tag_overrides():
    d = dish(("chicken thigh", 1, "lb"), tags=frozenset({"protein:vegetarian"}))
    assert f.protein(d) == "vegetarian"


def test_active_minutes_prefers_step_times():
    steps = (StepTime("Chop.", 10), StepTime("Simmer.", 5, 40))
    assert f.active_minutes(dish(steps=steps, prep_minutes=30)) == 15
    assert f.passive_minutes(dish(steps=steps)) == 40
    assert f.active_minutes(dish(prep_minutes=30)) == 30
    assert f.active_minutes(dish()) is None
    assert f.passive_minutes(dish(cook_minutes=25)) == 25


def test_equipment_recorded_or_inferred():
    assert f.equipment(dish(steps=(StepTime("x", equipment=("grill",)),))) == ("grill",)
    inferred = dish(steps=(StepTime("Roast the squash."), StepTime("Simmer the stock.")))
    assert f.equipment(inferred) == ("oven", "stove")
    assert f.equipment(dish(steps=(StepTime("Cook on low in the slow cooker."),))) == (
        "slow cooker",
    )


@pytest.mark.parametrize(
    ("ings", "servings", "tags", "level"),
    [
        ([("onion", 1, None)], 4, set(), 0),
        ([("berbere", 1, "tsp")], 4, set(), 2),
        ([("berbere", 0.25, "cup")], 6, set(), 3),  # Doro Wat: 2 tsp berbere per serving
        ([("jalapeño", 1, None)], 4, set(), 1),
        ([("jalapeño", 4, None)], 4, set(), 2),
        ([("mitmita", 0.5, "tsp")], 4, set(), 3),
        ([("berbere", 0.25, "cup")], 6, {"mild"}, 0),
        ([("onion", 1, None)], 4, {"very-spicy"}, 3),
        ([("cayenne pepper", None, None)], 4, set(), 2),
    ],
)
def test_spice_level(ings, servings, tags, level):
    assert f.spice_level(dish(*ings, servings=servings, tags=frozenset(tags))) == level


def test_season_fit():
    squash = dish(("butternut squash", 1, None))
    assert f.season_fit(squash, date(2026, 10, 4)) == 1.0
    assert f.season_fit(squash, date(2026, 7, 4)) == -1.0
    assert f.season_fit(dish(("onion", 1, None)), date(2026, 7, 4)) == 0.0


def test_leftover_friendly():
    assert f.leftover_friendly(dish(("chicken thigh", 1, "lb")), ["fish", "shellfish"])
    assert not f.leftover_friendly(dish(("salmon", 1, "lb")), ["fish", "shellfish"])
    no = dish(("chicken thigh", 1, "lb"), tags=frozenset({"no-leftovers"}))
    assert not f.leftover_friendly(no, [])
