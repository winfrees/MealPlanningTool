"""First run (UI-8): bring a new install to a usable state without the command line.

`prepare` loads the reference data every install needs (ingredient catalog, prep components,
house meals). `load_library` adds approved recipes from a library JSON file, the format of
`data/sample_library.json` (also the golden week's library, NFR-1). `populate_demo` builds the
sample household that `mealctl serve --demo` runs on.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mealplan.core import library, plan_store
from mealplan.core.base_week import seed_house_meals
from mealplan.core.components import load_components_csv, seed_components
from mealplan.core.normalizer import Catalog, seed_catalog
from mealplan.core.preferences import load_prefs
from mealplan.ingest import review_queue
from mealplan.models.enums import Collection, MealRole, RecipeStatus, SourceKind
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef, StepDraft
from mealplan.models.tables import Ingredient, Recipe

SAMPLE_LIBRARY = "sample_library.json"
DEMO_SEED = 7
DEMO_RATING_DATE = date(2026, 9, 1)
HOUSE_PREFIX = "house-"


def prepare(session: Session, data_dir: Path) -> bool:
    """Load the catalog, prep components and house meals if the catalog is empty.
    Returns whether anything was loaded."""
    if session.scalar(select(func.count()).select_from(Ingredient)):
        return False
    seed_catalog(session, Catalog.from_csv(data_dir / "ingredients.csv"))
    seed_components(session, load_components_csv(data_dir / "components.csv"))
    seed_house_meals(session, data_dir / "house_meals.json", Catalog.from_db(session))
    session.flush()
    return True


def load_library_json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def load_library(
    session: Session,
    catalog: Catalog,
    recipes: list[dict[str, Any]],
    rated_on: date = DEMO_RATING_DATE,
    source_title: str = "",
) -> list[str]:
    """Add library-format recipes through the normal draft path, approved. Returns refs."""
    refs = []
    for raw in recipes:
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
            sources=[SourceRef(kind=SourceKind.MANUAL, title=source_title)],
        )
        recipe = library.create_draft(session, draft, catalog, ref=raw["ref"])
        review_queue.approve(session, recipe)
        if raw.get("family"):
            library.add_to_family(session, recipe, raw["family"])
        for score in raw.get("ratings", []):
            library.rate(session, recipe, score, rated_on)
        refs.append(raw["ref"])
    session.flush()
    return refs


def populate_demo(session: Session, data_dir: Path, today: date) -> None:
    """The sample household: reference data, the sample library, and this week planned."""
    prepare(session, data_dir)
    data = load_library_json(data_dir / SAMPLE_LIBRARY)
    load_library(session, Catalog.from_db(session), data["recipes"], source_title="Sample recipe")
    prefs = load_prefs(session)
    plan_store.plan_and_save(session, plan_store.next_prep_day(today, prefs), prefs, seed=DEMO_SEED)


@dataclass(frozen=True)
class LibraryStatus:
    approved: int  # approved recipes, not counting the house meals
    drafts: int


def library_status(session: Session) -> LibraryStatus:
    def count(status: RecipeStatus) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(Recipe)
                .where(Recipe.status == status, Recipe.ref.not_like(f"{HOUSE_PREFIX}%"))
            )
            or 0
        )

    return LibraryStatus(count(RecipeStatus.APPROVED), count(RecipeStatus.DRAFT))


def approve_ready(session: Session) -> list[str]:
    """Approve every draft with no issues and no look-alike in the library (ING-3):
    the ones a reviewer would approve without a second look. Returns their refs."""
    approved = []
    for recipe in review_queue.pending(session):
        item = review_queue.review(session, recipe)
        if not item.issues and not item.similar:
            review_queue.approve(session, recipe)
            approved.append(recipe.ref)
    return approved
