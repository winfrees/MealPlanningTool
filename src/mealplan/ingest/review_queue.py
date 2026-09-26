"""ING-3: the review queue. Every import is a draft until a person approves it."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.core.library import LibraryError, Similar, similar_recipes
from mealplan.models.enums import RecipeStatus
from mealplan.models.tables import Recipe


@dataclass(frozen=True)
class ReviewItem:
    recipe: Recipe
    issues: list[str]
    similar: list[Similar]


def issues(recipe: Recipe) -> list[str]:
    """Problems a reviewer should look at before approving."""
    found = []
    if not recipe.sources:
        found.append("no source attribution (ING-4)")
    if not recipe.ingredients:
        found.append("no ingredients")
    if not recipe.steps:
        found.append("no steps")
    for ing in recipe.ingredients:
        if ing.ingredient_id is None:
            found.append(f"unmatched ingredient: {ing.raw_text!r}")
        elif ing.match_method == "trailing":
            found.append(f"loose ingredient match, check it: {ing.raw_text!r}")
        if ing.qty is None and "to taste" not in ing.prep_note and not ing.optional:
            found.append(f"no quantity: {ing.raw_text!r}")
    return found


def pending(session: Session) -> list[Recipe]:
    return list(
        session.scalars(
            select(Recipe).where(Recipe.status == RecipeStatus.DRAFT).order_by(Recipe.ref)
        )
    )


def review(session: Session, recipe: Recipe) -> ReviewItem:
    return ReviewItem(recipe, issues(recipe), similar_recipes(session, recipe))


def approve(session: Session, recipe: Recipe) -> None:
    """A person's action. Blocking problems (no source) stop it; the rest are advisory."""
    if recipe.status is not RecipeStatus.DRAFT:
        raise LibraryError(f"{recipe.ref} is already {recipe.status}")
    if not recipe.sources:
        raise LibraryError(f"{recipe.ref} has no source attribution (ING-4)")
    recipe.status = RecipeStatus.APPROVED
    session.flush()


def reject(session: Session, recipe: Recipe) -> None:
    if recipe.status is not RecipeStatus.DRAFT:
        raise LibraryError(f"{recipe.ref} is approved; only drafts can be rejected")
    session.delete(recipe)
    session.flush()
