"""Kitchen inventory (INV-1, INV-3, INV-4, INV-5).

Items are always catalog ingredients; a name the catalog cannot match is refused rather than
guessed. Staples (salt, oil, spices) are assumed on hand unless marked out.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.core.normalizer import Catalog
from mealplan.core.recipe_facts import DishIngredient
from mealplan.core.units import canonical_unit, try_convert
from mealplan.models.enums import InventorySource, Location
from mealplan.models.tables import Ingredient, InventoryItem, Preference

FREEZER_SHELF_DAYS = 90
STAPLE_CHECK_DAYS = 30
EXPIRING_DAYS = 3
_STAPLES_OUT = "staples_out"
_STAPLES_CHECKED = "staples_checked_on"


class InventoryError(ValueError):
    pass


def _ingredient(session: Session, catalog: Catalog, name: str) -> Ingredient:
    match = catalog.match(name)
    if match is None:
        raise InventoryError(f"{name!r} is not in the ingredient catalog; add it or use its name")
    row = session.scalars(
        select(Ingredient).where(Ingredient.canonical_name == match.entry.canonical_name)
    ).one_or_none()
    if row is None:
        raise InventoryError("ingredient catalog is not seeded; run `mealctl catalog seed`")
    return row


def _unit(unit: str | None) -> str:
    if unit is None or unit in ("", "each"):
        return "each"
    canonical = canonical_unit(unit)
    if canonical is None:
        raise InventoryError(f"unknown unit {unit!r}")
    return canonical


def add_item(
    session: Session,
    catalog: Catalog,
    name: str,
    qty: float,
    unit: str | None,
    location: Location,
    today: date,
    best_by: date | None = None,
    source: InventorySource = InventorySource.MANUAL,
    confidence: float = 1.0,
) -> InventoryItem:
    """INV-1. Best-by defaults from the catalog shelf life (90 days in the freezer)."""
    if qty <= 0:
        raise InventoryError("quantity must be positive")
    ingredient = _ingredient(session, catalog, name)
    if best_by is None:
        days = FREEZER_SHELF_DAYS if location is Location.FREEZER else ingredient.shelf_life_days
        best_by = today + timedelta(days=days) if days else None
    item = InventoryItem(
        ingredient_id=ingredient.id,
        qty=qty,
        unit=_unit(unit),
        location=location,
        added_on=today,
        best_by=best_by,
        source=source,
        confidence=confidence,
    )
    session.add(item)
    session.flush()
    return item


def items(session: Session) -> list[InventoryItem]:
    return list(
        session.scalars(
            select(InventoryItem)
            .join(Ingredient)
            .order_by(InventoryItem.location, InventoryItem.best_by, Ingredient.canonical_name)
        )
    )


def remove_item(session: Session, item_id: int) -> None:
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise InventoryError(f"no inventory item {item_id}")
    session.delete(item)
    session.flush()


def set_qty(session: Session, item_id: int, qty: float) -> None:
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise InventoryError(f"no inventory item {item_id}")
    if qty <= 0:
        session.delete(item)
    else:
        item.qty = qty
    session.flush()


def expiring(session: Session, today: date, days: int = EXPIRING_DAYS) -> list[InventoryItem]:
    """INV-5: items at or near their best-by date (these also steer the planner, PLN-2)."""
    return list(
        session.scalars(
            select(InventoryItem)
            .where(
                InventoryItem.best_by.is_not(None),
                InventoryItem.best_by <= today + timedelta(days=days),
            )
            .order_by(InventoryItem.best_by)
        )
    )


@dataclass(frozen=True)
class Shortfall:
    name: str
    qty: float
    unit: str


def deduct(session: Session, uses: list[DishIngredient]) -> list[Shortfall]:
    """INV-3: take used ingredients out of inventory, soonest best-by first. Staples and
    unmatched lines are skipped. Returns what inventory did not cover (not an error)."""
    names = {i.name for i in uses if i.matched and i.qty}
    ingredients = {
        i.canonical_name: i
        for i in session.scalars(select(Ingredient).where(Ingredient.canonical_name.in_(names)))
    }
    short: list[Shortfall] = []
    for use in uses:
        ingredient = ingredients.get(use.name)
        if ingredient is None or ingredient.is_staple or not use.qty:
            continue
        remaining = use.qty
        stock = session.scalars(
            select(InventoryItem)
            .where(InventoryItem.ingredient_id == ingredient.id)
            .order_by(InventoryItem.best_by.is_(None), InventoryItem.best_by, InventoryItem.id)
        )
        for item in stock:
            if remaining <= 1e-9:
                break
            have = try_convert(item.qty, item.unit, use.unit, ingredient.density_g_per_ml)
            if have is None or have <= 0:
                continue
            taken = min(have, remaining)
            remaining -= taken
            left = try_convert(have - taken, use.unit, item.unit, ingredient.density_g_per_ml)
            if left is None or left <= 1e-9:
                session.delete(item)
            else:
                item.qty = round(left, 6)
        if remaining > 1e-9:
            short.append(Shortfall(use.name, round(remaining, 6), use.unit or "each"))
    session.flush()
    return short


# --- staples (INV-4) -------------------------------------------------------------------------


@dataclass(frozen=True)
class StapleStatus:
    staples: tuple[str, ...]
    out: tuple[str, ...]
    last_checked: date | None
    check_due: bool


def _pref(session: Session, key: str) -> object:
    row = session.get(Preference, key)
    return row.value if row is not None else None


def _set_pref(session: Session, key: str, value: object) -> None:
    row = session.get(Preference, key)
    if row is None:
        session.add(Preference(key=key, value=value))
    else:
        row.value = value
    session.flush()


def staple_status(session: Session, today: date) -> StapleStatus:
    staples = tuple(
        session.scalars(
            select(Ingredient.canonical_name)
            .where(Ingredient.is_staple)
            .order_by(Ingredient.canonical_name)
        )
    )
    raw_out = _pref(session, _STAPLES_OUT)
    out = tuple(sorted(str(n) for n in raw_out)) if isinstance(raw_out, list) else ()
    raw_checked = _pref(session, _STAPLES_CHECKED)
    checked = date.fromisoformat(raw_checked) if isinstance(raw_checked, str) else None
    due = checked is None or (today - checked).days >= STAPLE_CHECK_DAYS
    return StapleStatus(staples, out, checked, due)


def check_staples(session: Session, catalog: Catalog, today: date, out: list[str]) -> StapleStatus:
    """Record a staples check: the named staples are out (they go on the next list)."""
    names = []
    for name in out:
        ingredient = _ingredient(session, catalog, name)
        if not ingredient.is_staple:
            raise InventoryError(f"{ingredient.canonical_name} is not a staple")
        names.append(ingredient.canonical_name)
    _set_pref(session, _STAPLES_OUT, sorted(set(names)))
    _set_pref(session, _STAPLES_CHECKED, today.isoformat())
    return staple_status(session, today)
