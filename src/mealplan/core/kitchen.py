"""Database side of M3: build and save the week's list; deduct what cooking and prep use."""

from collections import defaultdict
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mealplan.core import inventory, plan_store
from mealplan.core.inventory import Shortfall
from mealplan.core.normalizer import Catalog
from mealplan.core.planner import PlannedMeal
from mealplan.core.preferences import load_prefs
from mealplan.core.shopping import (
    ShoppingListResult,
    build_list,
    component_needs,
    dinner_needs,
    week_needs,
)
from mealplan.models.enums import Meal
from mealplan.models.tables import (
    Ingredient,
    InventoryItem,
    MealSlot,
    Preference,
    ShoppingLine,
    ShoppingList,
)


def on_hand(session: Session) -> dict[str, list[tuple[float, str]]]:
    held: dict[str, list[tuple[float, str]]] = defaultdict(list)
    rows = session.execute(
        select(Ingredient.canonical_name, InventoryItem.qty, InventoryItem.unit)
        .join(InventoryItem, InventoryItem.ingredient_id == Ingredient.id)
        .order_by(Ingredient.canonical_name, InventoryItem.id)
    )
    for name, qty, unit in rows:
        held[name].append((qty, unit))
    return dict(held)


def shopping_list(session: Session, week_start: date, today: date) -> ShoppingListResult:
    """SHP-1..4 for a saved week, saved as the week's shopping list."""
    result = plan_store.saved_plan(session, week_start)
    dishes = plan_store.plan_dishes(session, result)
    catalog = Catalog.from_db(session)
    staples_out = set(inventory.staple_status(session, today).out)
    built = build_list(
        week_start,
        week_needs(result, dishes),
        catalog,
        on_hand(session),
        staples_out,
        load_prefs(session).store_layout,
    )
    save_list(session, built)
    return built


def save_list(session: Session, result: ShoppingListResult) -> ShoppingList:
    row = session.scalars(
        select(ShoppingList).where(ShoppingList.week_start == result.week_start)
    ).one_or_none()
    if row is None:
        row = ShoppingList(week_start=result.week_start)
        session.add(row)
        session.flush()
    session.execute(delete(ShoppingLine).where(ShoppingLine.shopping_list_id == row.id))
    ids = {n: i for n, i in session.execute(select(Ingredient.canonical_name, Ingredient.id))}
    for line in (*result.lines, *result.have):
        session.add(
            ShoppingLine(
                shopping_list_id=row.id,
                ingredient_id=ids.get(line.ingredient) if line.ingredient else None,
                name=line.name,
                unit=line.unit,
                qty_needed=line.needed,
                qty_on_hand=line.on_hand,
                qty_to_buy=line.to_buy,
                pack_size=line.pack_size,
                pack_unit=line.pack_unit,
                packs=line.packs,
                note="; ".join(line.notes),
                section=line.section,
            )
        )
    session.flush()
    return row


def cook(session: Session, day: date, meal: Meal = Meal.DINNER) -> list[Shortfall]:
    """Mark a planned meal cooked and deduct what it used from inventory (INV-3).
    Marking it again deducts nothing."""
    slot = session.scalars(
        select(MealSlot).where(MealSlot.date == day, MealSlot.meal == meal)
    ).one_or_none()
    already = slot is not None and slot.cooked
    slot = plan_store.mark_cooked(session, day, meal)
    if already or slot.is_leftover_of is not None or slot.recipe is None:
        return []
    dish = plan_store.dish_from_recipe(session, slot.recipe)
    planned = PlannedMeal(slot.date, slot.meal, dish.ref, dish.title, slot.servings)
    return inventory.deduct(session, [n.ingredient for n in dinner_needs(planned, dish)])


def prep_done(session: Session, week_start: date) -> list[Shortfall]:
    """Deduct what the week's prep session used (once per week)."""
    key = f"prep_done:{week_start.isoformat()}"
    if session.get(Preference, key) is not None:
        return []
    result = plan_store.saved_plan(session, week_start)
    dishes = plan_store.plan_dishes(session, result)
    uses = [n.ingredient for use in result.components for n in component_needs(use, dishes)]
    short = inventory.deduct(session, uses)
    session.add(Preference(key=key, value=True))
    session.flush()
    return short
