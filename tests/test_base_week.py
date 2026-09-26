"""The household base week: Monday salmon, Tuesday tacos (alternating), Friday pizza."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from mealplan.core import library
from mealplan.core.base_week import pick_rotation, rotation, seed_house_meals
from mealplan.core.planner import PlannedMeal, WeekPlanResult, plan_week
from mealplan.core.preferences import HouseholdPrefs, Weekday
from mealplan.models.enums import Meal
from mealplan.models.tables import Recipe
from tests.planning_fixtures import ROOT, golden_inputs, golden_start

START = golden_start()  # Sunday
MON, TUE, FRI = (START + timedelta(days=n) for n in (1, 2, 5))


def dinner_on(plan: WeekPlanResult, day: date) -> PlannedMeal:
    return next(m for m in plan.meals if m.date == day and m.meal is Meal.DINNER)


@pytest.fixture(scope="module")
def plan(catalog):
    return plan_week(golden_inputs(catalog), HouseholdPrefs(), START, 7)


def test_standing_meals(plan):
    assert dinner_on(plan, MON).ref == "house-001"
    assert dinner_on(plan, TUE).ref in {"house-002", "house-003"}
    assert dinner_on(plan, FRI).ref == "house-004"


def test_house_meals_only_on_their_days(catalog):
    for seed in range(20):
        plan = plan_week(golden_inputs(catalog), HouseholdPrefs(), START, seed)
        for m in plan.meals:
            if m.meal is Meal.DINNER and m.date not in (MON, TUE, FRI):
                assert not (m.ref or "").startswith("house-"), (seed, m)


@pytest.mark.parametrize(
    ("last", "expected"), [("house-003", "house-002"), ("house-002", "house-003")]
)
def test_tacos_alternate_week_to_week(catalog, last, expected):
    history = {last: TUE - timedelta(days=7)}
    plan = plan_week(golden_inputs(catalog, history=history), HouseholdPrefs(), START, 7)
    assert dinner_on(plan, TUE).ref == expected


def test_standing_meals_ignore_the_repeat_window(catalog):
    history = {"house-001": MON - timedelta(days=7), "house-004": FRI - timedelta(days=7)}
    plan = plan_week(golden_inputs(catalog, history=history), HouseholdPrefs(), START, 7)
    assert dinner_on(plan, MON).ref == "house-001"
    assert dinner_on(plan, FRI).ref == "house-004"
    assert not any("repeat window" in c for c in plan.conflicts)


def test_a_swap_beats_the_base_week(catalog):
    inputs = golden_inputs(catalog, locked={(MON, Meal.DINNER): "core-079"})
    plan = plan_week(inputs, HouseholdPrefs(), START, 7)
    assert dinner_on(plan, MON).ref == "core-079"


def test_missing_house_meal_is_reported_and_filled(catalog):
    plan = plan_week(golden_inputs(catalog, drop={"house-001"}), HouseholdPrefs(), START, 7)
    assert any("house-001 is not approved" in c for c in plan.conflicts)
    assert dinner_on(plan, MON).ref is not None


def test_pizza_night_uses_no_protein_allowance_and_leaves_no_leftovers(plan):
    sat_lunch = [m for m in plan.meals if m.meal is Meal.LUNCH and m.leftover_of == FRI]
    assert sat_lunch == []
    assert dinner_on(plan, FRI).servings == 4


def test_empty_base_week_plans_every_day(catalog):
    prefs = HouseholdPrefs(base_week={})
    plan = plan_week(golden_inputs(catalog), prefs, START, 7)
    assert all(m.ref for m in plan.meals if m.meal is Meal.DINNER)
    assert plan.conflicts == ()


def test_rotation_helpers():
    assert rotation("house-002 | house-003") == ["house-002", "house-003"]
    assert pick_rotation(["a"], {}, START) == "a"
    even, odd = START, START + timedelta(days=7)
    assert {pick_rotation(["a", "b"], {}, even), pick_rotation(["a", "b"], {}, odd)} == {"a", "b"}
    history = {"a": date(2026, 9, 1), "b": date(2026, 9, 8)}
    assert pick_rotation(["a", "b"], history, START) == "a"


def test_default_base_week_is_the_household_schedule():
    base = HouseholdPrefs().base_week
    assert base == {
        Weekday.MON: "house-001",
        Weekday.TUE: "house-002|house-003",
        Weekday.FRI: "house-004",
    }


def test_seed_house_meals_is_idempotent_and_never_overwrites(session, catalog):
    path = ROOT / "data" / "house_meals.json"
    assert seed_house_meals(session, path, catalog) == [
        "house-001",
        "house-002",
        "house-003",
        "house-004",
    ]
    salmon = library.get_recipe(session, "house-001")
    salmon.title = "Salmon Night"
    assert seed_house_meals(session, path, catalog) == []
    assert library.get_recipe(session, "house-001").title == "Salmon Night"
    tacos = session.scalars(select(Recipe).where(Recipe.ref.in_(["house-002", "house-003"])))
    assert {t.family.name for t in tacos if t.family} == {"tacos"}
    assert all(r.status.value == "approved" for r in session.scalars(select(Recipe)))
