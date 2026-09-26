"""Inventory: add, expiring, deduct on use, staples (INV-1, 3, 4, 5)."""

from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from mealplan.core import inventory
from mealplan.core.inventory import InventoryError
from mealplan.core.normalizer import Catalog
from mealplan.core.recipe_facts import DishIngredient
from mealplan.models.enums import Location
from mealplan.models.tables import Ingredient, InventoryItem

TODAY = date(2026, 10, 3)


def add(
    session: Session,
    catalog: Catalog,
    name: str,
    qty: float,
    unit: str | None,
    location: Location = Location.FRIDGE,
    **kw: Any,
) -> InventoryItem:
    return inventory.add_item(session, catalog, name, qty, unit, location, TODAY, **kw)


def test_add_matches_the_catalog_and_defaults_best_by(session, catalog):
    item = add(session, catalog, "baby spinach", 5, "oz")
    assert session.get(Ingredient, item.ingredient_id).canonical_name == "spinach"
    assert item.unit == "oz"
    assert item.best_by == date(2026, 10, 8)  # spinach keeps 5 days
    frozen = add(session, catalog, "ground beef", 1, "lb", Location.FREEZER)
    assert frozen.best_by == date(2027, 1, 1)  # 90 days in the freezer
    eggs = add(session, catalog, "eggs", 12, None)
    assert eggs.unit == "each"


def test_add_refuses_what_it_cannot_match(session, catalog):
    with pytest.raises(InventoryError, match="not in the ingredient catalog"):
        add(session, catalog, "unobtainium", 1, "lb")
    with pytest.raises(InventoryError, match="unknown unit"):
        add(session, catalog, "onion", 1, "furlong")
    with pytest.raises(InventoryError, match="positive"):
        add(session, catalog, "onion", 0, None)


def test_expiring_report(session, catalog):
    add(session, catalog, "spinach", 5, "oz")  # best by Oct 8
    add(session, catalog, "milk", 1, "gallon", best_by=date(2026, 10, 5))
    add(session, catalog, "rice", 2, "lb")  # keeps two years
    soon = inventory.expiring(session, TODAY)
    assert [i.best_by for i in soon] == [date(2026, 10, 5)]
    assert len(inventory.expiring(session, TODAY, days=5)) == 2


def test_deduct_uses_soonest_first_and_converts_units(session, catalog):
    older = add(session, catalog, "chicken thigh", 1, "lb", best_by=date(2026, 10, 4))
    newer = add(session, catalog, "chicken thigh", 2, "lb", best_by=date(2026, 10, 6))
    short = inventory.deduct(session, [DishIngredient("chicken thigh", 24, "oz")])
    assert short == []
    assert inventory.items(session) == [newer]
    assert newer.qty == pytest.approx(1.5)
    assert older not in inventory.items(session)


def test_deduct_reports_shortfalls_and_skips_staples(session, catalog):
    add(session, catalog, "olive oil", 1, "cup")
    add(session, catalog, "onion", 1, None)
    short = inventory.deduct(
        session,
        [
            DishIngredient("olive oil", 2, "tbsp"),  # staple: assumed on hand
            DishIngredient("onion", 3, None),
            DishIngredient("2 cups unobtainium", 2, "cup", matched=False),
        ],
    )
    assert short == [inventory.Shortfall("onion", 2, "each")]
    remaining = {i.ingredient_id: i.qty for i in inventory.items(session)}
    assert list(remaining.values()) == [1]  # the olive oil, untouched


def test_set_and_remove(session, catalog):
    item = add(session, catalog, "onion", 3, None)
    inventory.set_qty(session, item.id, 1)
    assert item.qty == 1
    inventory.set_qty(session, item.id, 0)
    assert inventory.items(session) == []
    with pytest.raises(InventoryError):
        inventory.remove_item(session, 999)


def test_staples_check(session, catalog):
    status = inventory.staple_status(session, TODAY)
    assert "salt" in status.staples and "olive oil" in status.staples
    assert status.out == () and status.check_due
    status = inventory.check_staples(session, catalog, TODAY, ["kosher salt", "EVOO"])
    assert status.out == ("kosher salt", "olive oil")
    assert not status.check_due and status.last_checked == TODAY
    assert inventory.staple_status(session, date(2026, 11, 2)).check_due
    with pytest.raises(InventoryError, match="not a staple"):
        inventory.check_staples(session, catalog, TODAY, ["onion"])
