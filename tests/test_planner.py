"""Planner rules on the golden week (PLN-1, 2, 3, 4, 7, 8; REC-9)."""

from collections import Counter
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mealplan.core import recipe_facts
from mealplan.core.planner import PlanInputs, PlannedMeal, WeekPlanResult, plan_week
from mealplan.core.preferences import HouseholdPrefs
from mealplan.core.recipe_facts import Dish
from mealplan.models.enums import Collection, Meal
from tests.planning_fixtures import golden_inputs, golden_seed, golden_start

START = golden_start()
WEEKNIGHTS = {START + timedelta(days=i) for i in range(1, 6)}  # Mon-Fri


@pytest.fixture(scope="module")
def inputs(catalog):
    return golden_inputs(catalog)


@pytest.fixture(scope="module")
def plan(inputs):
    return plan_week(inputs, HouseholdPrefs(), START, golden_seed())


def dinners(plan: WeekPlanResult) -> list[PlannedMeal]:
    return [m for m in plan.meals if m.meal is Meal.DINNER]


def lunches(plan: WeekPlanResult) -> list[PlannedMeal]:
    return [m for m in plan.meals if m.meal is Meal.LUNCH]


def by_ref(inputs: PlanInputs) -> dict[str | None, Dish]:
    return {d.ref: d for d in inputs.dishes}


def test_seven_dinners_sunday_to_saturday(plan):
    days = [m.date for m in dinners(plan)]
    assert days == [START + timedelta(days=i) for i in range(7)]
    assert all(m.ref for m in dinners(plan))


def test_hard_rules(plan, inputs):
    dishes = by_ref(inputs)
    refs = [m.ref for m in dinners(plan)]
    assert len(refs) == len(set(refs)), "no dish twice in a week"
    assert "core-001" not in refs, "very spicy Doro Wat is excluded"
    assert "core-023" not in refs, "cooked within the 14-day repeat window"
    discovered = [r for r in refs if dishes[r].collection is Collection.DISCOVERED]
    assert len(discovered) <= 1, "PLN-8"
    proteins = Counter(
        recipe_facts.protein(dishes[r]) for r in refs if "order-in" not in dishes[r].tags
    )
    assert max(proteins.values()) <= 2, proteins
    families = [dishes[r].family for r in refs if dishes[r].family]
    assert len(families) == len(set(families)), "REC-9: a family is one dish"
    assert "core-040" not in refs, "shakshuka family plays its preferred variant"
    assert "core-055" not in refs, "butter chicken family plays its rated variant"


def test_weeknight_time_limit(plan, inputs):
    dishes = by_ref(inputs)
    for m in dinners(plan):
        if m.date in WEEKNIGHTS:
            minutes = recipe_facts.active_minutes(dishes[m.ref])
            assert minutes is None or minutes <= 45, (m.date, m.ref, minutes)


def test_leftovers_feed_next_day_lunches(plan, inputs):
    dishes = by_ref(inputs)
    by_date = {m.date: m for m in dinners(plan)}
    leftovers = [m for m in lunches(plan) if m.leftover_of is not None]
    assert 1 <= len(leftovers) <= 3
    for lunch in leftovers:
        assert lunch.leftover_of is not None
        dinner = by_date[lunch.leftover_of]
        assert lunch.leftover_of == lunch.date - timedelta(days=1)
        assert lunch.ref == dinner.ref and lunch.servings == 2
        assert dinner.servings == 6, "cooked for 4 tonight plus 2 lunches"
        assert recipe_facts.protein(dishes[dinner.ref]) not in ("fish", "shellfish")
    fed = {m.leftover_of for m in leftovers}
    for d in dinners(plan):
        if d.date not in fed:
            assert d.servings == 4


def test_every_weekday_has_a_lunch(plan):
    days = sorted(m.date for m in lunches(plan))
    assert days == [START + timedelta(days=i) for i in range(1, 6)]
    for m in lunches(plan):
        assert m.leftover_of is not None or m.components, m
        assert m.servings == 2


def test_template_lunches_are_early_in_the_week(plan):
    template_days = [m.date for m in lunches(plan) if m.leftover_of is None]
    leftover_days = [m.date for m in lunches(plan) if m.leftover_of is not None]
    assert max(template_days) < min(leftover_days)


def test_expiring_spinach_is_used(plan, inputs):
    dishes = by_ref(inputs)
    assert any(
        any(i.name == "spinach" for i in dishes[m.ref].ingredients) for m in dinners(plan)
    ), "PLN-2"


def test_same_seed_same_plan_different_seed_still_valid(inputs, plan):
    again = plan_week(inputs, HouseholdPrefs(), START, golden_seed())
    assert again == plan
    other = plan_week(inputs, HouseholdPrefs(), START, golden_seed() + 1)
    assert [m.date for m in dinners(other)] == [m.date for m in dinners(plan)]


def test_locked_slots_are_kept(catalog):
    wed = START + timedelta(days=3)
    locked = {(wed, Meal.DINNER): "core-079"}
    plan = plan_week(golden_inputs(catalog, locked=locked), HouseholdPrefs(), START, 7)
    wed_dinner = next(m for m in dinners(plan) if m.date == wed)
    assert wed_dinner.ref == "core-079" and wed_dinner.locked


def test_locked_unknown_recipe_is_a_conflict(catalog):
    locked = {(START, Meal.DINNER): "core-999"}
    plan = plan_week(golden_inputs(catalog, locked=locked), HouseholdPrefs(), START, 7)
    assert any("core-999" in c for c in plan.conflicts)


def test_prefs_change_the_plan(catalog, inputs):
    prefs = HouseholdPrefs(dinner_servings=2, lunch_servings=1, max_leftover_lunches=0)
    plan = plan_week(inputs, prefs, START, 7)
    assert all(m.leftover_of is None for m in lunches(plan))
    assert all(m.servings == 2 for m in dinners(plan))
    assert all(m.servings == 1 for m in lunches(plan))


def test_avoid_ingredient_is_never_planned(inputs):
    prefs = HouseholdPrefs(avoid_ingredients=["egg"])
    plan = plan_week(inputs, prefs, START, 7)
    dishes = by_ref(inputs)
    for m in plan.meals:
        if m.ref:
            assert all(i.name != "egg" for i in dishes[m.ref].ingredients), m.ref


def test_small_library_relaxes_and_reports(catalog):
    keep = {"core-024", "core-044", "core-073"}
    all_refs = {d.ref for d in golden_inputs(catalog).dishes}
    small = golden_inputs(catalog, drop=all_refs - keep)
    plan = plan_week(small, HouseholdPrefs(), START, 7)
    empty = [m for m in dinners(plan) if m.ref is None]
    assert len(empty) == 4
    assert sum("no dinner fits" in c for c in plan.conflicts) == 4


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_any_seed_satisfies_the_hard_rules(catalog, seed):
    inputs = golden_inputs(catalog)
    plan = plan_week(inputs, HouseholdPrefs(), START, seed)
    dishes = by_ref(inputs)
    refs = [m.ref for m in dinners(plan)]
    assert None not in refs
    assert len(set(refs)) == 7
    assert "core-001" not in refs and "core-023" not in refs
    cooked = [r for r in refs if "order-in" not in dishes[r].tags]
    assert max(Counter(recipe_facts.protein(dishes[r]) for r in cooked).values()) <= 2
    assert sum(dishes[r].collection is Collection.DISCOVERED for r in refs) <= 1
    for m in dinners(plan):
        if m.date in WEEKNIGHTS:
            minutes = recipe_facts.active_minutes(dishes[m.ref])
            assert minutes is None or minutes <= 45
