"""M3 through the database: list for a saved week, deduction on cooking and prep (INV-3)."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.core import inventory, kitchen, plan_store
from mealplan.core.preferences import HouseholdPrefs
from mealplan.core.render_list import shopping_markdown
from mealplan.models.enums import Location, Meal
from mealplan.models.tables import Ingredient, InventoryItem, ShoppingLine, ShoppingList
from tests.planning_fixtures import GOLDEN_WEEK, golden_seed, golden_start, populate_golden

START = golden_start()
TODAY = START - timedelta(days=1)


@pytest.fixture
def week(session, catalog):
    populate_golden(session, catalog)
    plan_store.plan_and_save(session, START, HouseholdPrefs(), seed=golden_seed())
    return session


def stock(session: Session, name: str) -> dict[str, float]:
    return {
        i.unit: i.qty
        for i in session.scalars(
            select(InventoryItem).join(Ingredient).where(Ingredient.canonical_name == name)
        )
    }


def test_database_list_matches_the_golden_list(week):
    result = kitchen.shopping_list(week, START, TODAY)
    assert shopping_markdown(result) == (GOLDEN_WEEK / "shopping.md").read_text(encoding="utf-8")


def test_list_is_saved_with_its_math(week):
    result = kitchen.shopping_list(week, START, TODAY)
    saved = week.scalars(select(ShoppingList).where(ShoppingList.week_start == START)).one()
    lines = {ln.name: ln for ln in week.scalars(select(ShoppingLine))}
    assert len(lines) == len(result.lines) + len(result.have)
    spinach = lines["spinach"]
    assert (spinach.unit, spinach.qty_needed, spinach.packs) == ("cup", 11.5, 2)
    assert spinach.qty_on_hand == pytest.approx(4.61, abs=0.01)
    kitchen.shopping_list(week, START, TODAY)  # rebuilding replaces, never duplicates
    assert len(week.scalars(select(ShoppingLine)).all()) == len(lines)
    assert saved.id == week.scalars(select(ShoppingList.id)).one()


def test_inventory_and_staples_change_the_list(week, catalog):
    inventory.add_item(week, catalog, "ground beef", 3, "lb", Location.FREEZER, TODAY)
    inventory.check_staples(week, catalog, TODAY, ["olive oil"])
    result = kitchen.shopping_list(week, START, TODAY)
    assert "ground beef" in {ln.name for ln in result.have}
    olive = next(ln for ln in result.lines if ln.name == "olive oil")
    assert olive.packs == 1
    assert "olive oil" not in {s.name for s in result.staples}


def test_cooking_deducts_the_dinner_once(week, catalog):
    inventory.add_item(week, catalog, "ground beef", 3, "lb", Location.FRIDGE, TODAY)
    tuesday = START + timedelta(days=2)  # tacos for 6: 1.5 lb x 1.5 = 2.25 lb
    short = kitchen.cook(week, tuesday)
    assert stock(week, "ground beef") == {"lb": pytest.approx(0.75)}
    assert {s.name for s in short} >= {"tortilla", "avocado"}  # never stocked
    assert kitchen.cook(week, tuesday) == []
    assert stock(week, "ground beef") == {"lb": pytest.approx(0.75)}


def test_leftover_lunch_and_order_in_deduct_nothing(week, catalog):
    inventory.add_item(week, catalog, "ground beef", 3, "lb", Location.FRIDGE, TODAY)
    assert kitchen.cook(week, START + timedelta(days=3), Meal.LUNCH) == []  # leftover tacos
    assert kitchen.cook(week, START + timedelta(days=5)) == []  # pizza night
    assert stock(week, "ground beef") == {"lb": 3}


def test_prep_done_deducts_components_once(week, catalog):
    inventory.add_item(week, catalog, "quinoa", 2, "cup", Location.PANTRY, TODAY)
    kitchen.prep_done(week, START)  # quinoa salad for 2 of 6 servings: 1/2 cup
    assert stock(week, "quinoa") == {"cup": pytest.approx(1.5)}
    assert kitchen.prep_done(week, START) == []
    assert stock(week, "quinoa") == {"cup": pytest.approx(1.5)}


def test_expiring_items_steer_next_weeks_plan(week, catalog):
    """INV-5 feeds PLN-2: expiring items show up in the planner's inputs."""
    nxt = START + timedelta(days=7)
    inventory.add_item(
        week, catalog, "eggplant", 2, None, Location.FRIDGE, TODAY, best_by=nxt + timedelta(days=1)
    )
    assert "eggplant" in plan_store.load_inputs(week, nxt).expiring
    assert date(2026, 10, 11) == nxt
