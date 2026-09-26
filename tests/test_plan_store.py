"""Planning through the database: load, save, reload, swap, lock (PLN-7)."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from mealplan.core import library, plan_store
from mealplan.core.components import load_components_csv, seed_components
from mealplan.core.plan_store import PlanError
from mealplan.core.planner import WeekPlanResult, plan_week
from mealplan.core.preferences import HouseholdPrefs
from mealplan.ingest import review_queue
from mealplan.models.enums import Collection, InventorySource, Location, Meal, MealRole, SourceKind
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef, StepDraft
from mealplan.models.tables import Ingredient, InventoryItem, MealSlot
from tests.planning_fixtures import ROOT, golden_inputs, golden_seed, golden_start, load_golden

START = golden_start()


def without_templates(result: WeekPlanResult) -> WeekPlanResult:
    return replace(result, meals=tuple(replace(m, template=None) for m in result.meals))


@pytest.fixture
def golden_db(session, catalog):
    """The golden library imported through the normal library path."""
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
    session.flush()
    return session


def test_database_inputs_plan_like_the_golden_fixture(golden_db, catalog):
    from_db = plan_week(
        plan_store.load_inputs(golden_db, START), HouseholdPrefs(), START, golden_seed()
    )
    from_json = plan_week(golden_inputs(catalog), HouseholdPrefs(), START, golden_seed())
    assert from_db == from_json


def test_save_and_reload_round_trip(golden_db):
    result = plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), seed=golden_seed())
    assert plan_store.saved_plan(golden_db, START) == without_templates(result)
    week = plan_store.get_week(golden_db, START)
    assert week is not None and (week.seed, week.status) == (golden_seed(), "draft")


def test_replan_reuses_the_stored_seed(golden_db):
    first = plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), seed=golden_seed())
    again = plan_store.plan_and_save(golden_db, START, HouseholdPrefs())
    assert again == first


def test_history_blocks_last_weeks_dinners(golden_db):
    plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), seed=golden_seed())
    first = {m.ref for m in plan_store.saved_plan(golden_db, START).meals if m.meal is Meal.DINNER}
    nxt = START + timedelta(days=7)
    second = plan_store.plan_and_save(golden_db, nxt, HouseholdPrefs())
    repeats = first & {m.ref for m in second.meals if m.meal is Meal.DINNER}
    assert not repeats or any("repeat window" in c for c in second.conflicts)


def test_swap_moves_only_the_swapped_dinner(golden_db):
    before = plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), seed=golden_seed())
    tue = START + timedelta(days=2)
    after = plan_store.swap(golden_db, START, tue, Meal.DINNER, "core-048", HouseholdPrefs())

    old = {m.date: m.ref for m in before.meals if m.meal is Meal.DINNER}
    new = {m.date: m.ref for m in after.meals if m.meal is Meal.DINNER}
    assert new[tue] == "core-048"
    assert {d: r for d, r in new.items() if d != tue} == {d: r for d, r in old.items() if d != tue}

    slots = golden_db.scalars(select(MealSlot).where(MealSlot.is_override)).all()
    assert [(s.date, s.meal) for s in slots] == [(tue, Meal.DINNER)]
    # A fresh re-plan keeps the override.
    replanned = plan_store.plan_and_save(golden_db, START, HouseholdPrefs())
    assert (
        next(m for m in replanned.meals if m.date == tue and m.meal is Meal.DINNER).ref
        == "core-048"
    )


def test_lock_blocks_replan_and_swap(golden_db):
    plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), seed=golden_seed())
    plan_store.set_locked(golden_db, START, True)
    with pytest.raises(PlanError, match="locked"):
        plan_store.plan_and_save(golden_db, START, HouseholdPrefs())
    with pytest.raises(PlanError, match="locked"):
        plan_store.swap(golden_db, START, START, Meal.DINNER, "core-048", HouseholdPrefs())
    plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), force=True)


def test_swap_rejects_bad_input(golden_db):
    plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), seed=golden_seed())
    with pytest.raises(PlanError, match="not in the week"):
        plan_store.swap(
            golden_db, START, START - timedelta(days=1), Meal.DINNER, "core-048", HouseholdPrefs()
        )
    with pytest.raises(library.LibraryError):
        plan_store.swap(golden_db, START, START, Meal.DINNER, "core-999", HouseholdPrefs())
    with pytest.raises(PlanError, match="no plan"):
        plan_store.swap(
            golden_db, START + timedelta(days=70), START, Meal.DINNER, "core-048", HouseholdPrefs()
        )


def test_mark_cooked(golden_db):
    plan_store.plan_and_save(golden_db, START, HouseholdPrefs(), seed=golden_seed())
    slot = plan_store.mark_cooked(golden_db, START)
    assert slot.cooked
    with pytest.raises(PlanError):
        plan_store.mark_cooked(golden_db, START - timedelta(days=30))
