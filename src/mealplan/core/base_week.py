"""The household's base week: standing meals every plan starts from.

`HouseholdPrefs.base_week` maps a weekday to a recipe ref ("house-001") or a rotation of refs
("house-002|house-003", used in turn week by week). Days not listed are chosen by the planner.
The standing meals themselves are house recipes seeded from `data/house_meals.json`.
"""

import json
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.core import library
from mealplan.core.normalizer import Catalog
from mealplan.ingest import review_queue
from mealplan.models.enums import Collection, MealRole, SourceKind
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef, StepDraft
from mealplan.models.tables import Recipe

ORDER_IN_TAG = "order-in"


def rotation(rule: str) -> list[str]:
    return [ref.strip() for ref in rule.split("|") if ref.strip()]


def pick_rotation(refs: list[str], history: dict[str, date], week_start: date) -> str:
    """The next ref in a rotation: the one after the most recently planned, or by week
    number when none of them has been planned yet. Deterministic either way."""
    if len(refs) == 1:
        return refs[0]
    used = [(history[r], i) for i, r in enumerate(refs) if r in history]
    if used:
        _, last = max(used)
        return refs[(last + 1) % len(refs)]
    return refs[(week_start.toordinal() // 7) % len(refs)]


def seed_house_meals(session: Session, path: Path, catalog: Catalog) -> list[str]:
    """Add the house recipes that are missing, approved, as core recipes (REC-7).
    Existing ones are left as they are (REC-8). Returns the refs added."""
    data = json.loads(path.read_text(encoding="utf-8"))
    existing = set(session.scalars(select(Recipe.ref)))
    added = []
    for raw in data["recipes"]:
        if raw["ref"] in existing:
            continue
        draft = RecipeDraft(
            title=raw["title"],
            servings=raw["servings"],
            meal_role=MealRole.DINNER,
            tags=raw.get("tags", []),
            collection=Collection.CORE,
            ingredients=[IngredientLine(raw_text=line) for line in raw["ingredients"]],
            steps=[
                StepDraft(text=t, active_minutes=a, passive_minutes=p, equipment=e)
                for t, a, p, e in raw["steps"]
            ],
            sources=[SourceRef(kind=SourceKind.MANUAL, title="Household base week")],
        )
        recipe = library.create_draft(session, draft, catalog, ref=raw["ref"])
        review_queue.approve(session, recipe)
        if raw.get("family"):
            library.add_to_family(session, recipe, raw["family"])
        added.append(raw["ref"])
    session.flush()
    return added
