"""Prep-ahead components and lunch templates (PLN-4).

Components are either generic (washed greens, cooked quinoa: `data/components.csv`) or a whole
soup- or lunch-role recipe made as a batch on prep day.
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.core import recipe_facts
from mealplan.core.recipe_facts import Dish, DishIngredient
from mealplan.core.units import canonical_unit
from mealplan.models.enums import MealRole
from mealplan.models.tables import Component

DEFAULT_KEEPS = {"soup": 4, "main": 4}


@dataclass(frozen=True)
class ComponentSpec:
    name: str
    kind: str  # soup, main, grain, protein, veg, salad-base, dressing
    keeps_days: int
    freezer_ok: bool
    active_minutes: int
    passive_minutes: int
    equipment: tuple[str, ...] = ()
    per_serving: tuple[DishIngredient, ...] = ()
    recipe_ref: str | None = None


@dataclass(frozen=True)
class LunchTemplate:
    name: str
    parts: tuple[str, ...]  # component kinds, or "soup" / "main" for recipe components


# Rotation order for lunch days not fed by leftovers.
TEMPLATES = (
    LunchTemplate("lunch-recipe", ("main",)),
    LunchTemplate("soup-and-salad", ("soup", "salad-base", "dressing")),
    LunchTemplate("grain-bowl", ("grain", "protein", "veg", "dressing")),
)


def _keeps(dish: Dish, kind: str) -> int:
    for tag in dish.tags:
        if (m := re.fullmatch(r"keeps:(\d+)", tag)) is not None:
            return int(m[1])
    return DEFAULT_KEEPS[kind]


def from_dish(dish: Dish) -> ComponentSpec:
    """A soup or lunch recipe as a batch component."""
    kind = "soup" if dish.role is MealRole.SOUP else "main"
    freezer_ok = "no-freeze" not in dish.tags if kind == "soup" else "freezer-friendly" in dish.tags
    return ComponentSpec(
        name=dish.title,
        kind=kind,
        keeps_days=_keeps(dish, kind),
        freezer_ok=freezer_ok,
        active_minutes=recipe_facts.active_minutes(dish) or 20,
        passive_minutes=recipe_facts.passive_minutes(dish),
        equipment=recipe_facts.equipment(dish),
        recipe_ref=dish.ref,
    )


def _per_serving(text: str) -> tuple[DishIngredient, ...]:
    items = []
    for part in text.split(";"):
        if not part.strip():
            continue
        name, qty, unit = (p.strip() for p in part.split(":"))
        items.append(DishIngredient(name, float(qty), canonical_unit(unit) if unit else None))
    return tuple(items)


def load_components_csv(path: Path) -> list[ComponentSpec]:
    with path.open(encoding="utf-8", newline="") as f:
        return [
            ComponentSpec(
                name=row["name"].strip(),
                kind=row["kind"].strip(),
                keeps_days=int(row["keeps_days"]),
                freezer_ok=row["freezer_ok"].strip().lower() == "yes",
                active_minutes=int(row["active_minutes"]),
                passive_minutes=int(row["passive_minutes"]),
                equipment=tuple(e.strip() for e in row["equipment"].split(";") if e.strip()),
                per_serving=_per_serving(row["per_serving"]),
            )
            for row in csv.DictReader(f)
        ]


def seed_components(session: Session, specs: list[ComponentSpec]) -> tuple[int, int]:
    """Insert or update generic components by name. Returns (added, updated)."""
    existing = {c.name: c for c in session.scalars(select(Component))}
    added = updated = 0
    for spec in specs:
        row = existing.get(spec.name)
        if row is None:
            row = Component(name=spec.name)
            session.add(row)
            added += 1
        else:
            updated += 1
        row.kind = spec.kind
        row.keeps_days = spec.keeps_days
        row.freezer_ok = spec.freezer_ok
        row.active_minutes = spec.active_minutes
        row.passive_minutes = spec.passive_minutes
        row.equipment = list(spec.equipment)
        row.per_serving = [[i.name, i.qty, i.unit] for i in spec.per_serving]
    session.flush()
    return added, updated


def generic_from_db(session: Session) -> list[ComponentSpec]:
    rows = session.scalars(
        select(Component).where(Component.recipe_id.is_(None)).order_by(Component.name)
    )
    return [
        ComponentSpec(
            name=c.name,
            kind=c.kind,
            keeps_days=c.keeps_days or 4,
            freezer_ok=c.freezer_ok,
            active_minutes=c.active_minutes,
            passive_minutes=c.passive_minutes,
            equipment=tuple(c.equipment),
            per_serving=tuple(
                DishIngredient(str(n), float(q) if q is not None else None, u)  # type: ignore[arg-type]
                for n, q, u in c.per_serving
            ),
        )
        for c in rows
    ]
