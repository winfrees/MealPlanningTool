"""Recipe library: drafts, copies vs variants, families, ratings, favorites, promotion.

REC-3 copies vs variants, REC-5 favorites, REC-7 collections and promotion, REC-8 core is the
source of truth, REC-9 variant families, ING-3 drafts, ING-4 attribution.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mealplan.core.normalizer import DESCRIPTORS, Catalog
from mealplan.core.parser import parse_ingredient
from mealplan.models.enums import Collection, RecipeStatus
from mealplan.models.schemas import IngredientLine, RecipeDraft, RecipeEdit
from mealplan.models.tables import (
    Ingredient,
    Rating,
    Recipe,
    RecipeFamily,
    RecipeIngredient,
    RecipeSource,
    Step,
)

REF_PREFIX = {Collection.CORE: "core", Collection.DISCOVERED: "disc"}
SIMILARITY_THRESHOLD = 0.6
PROMOTION_MIN_SCORE = 4


class LibraryError(ValueError):
    pass


def get_recipe(session: Session, ref: str) -> Recipe:
    recipe = session.scalars(select(Recipe).where(Recipe.ref == ref)).one_or_none()
    if recipe is None:
        raise LibraryError(f"no recipe {ref!r}")
    return recipe


def next_ref(session: Session, collection: Collection) -> str:
    prefix = REF_PREFIX[collection]
    refs = session.scalars(select(Recipe.ref).where(Recipe.ref.like(f"{prefix}-%")))
    numbers = [int(r.split("-", 1)[1]) for r in refs if r.split("-", 1)[1].isdigit()]
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def _ingredient_row(
    session: Session, catalog: Catalog, line: IngredientLine, position: int
) -> RecipeIngredient:
    if line.name is None:
        parsed = parse_ingredient(line.raw_text)
        name, qty, unit = parsed.name, parsed.qty, parsed.unit
        prep_note, optional = parsed.prep_note, parsed.optional or line.optional
    else:
        name, qty, unit = line.name, line.qty, line.unit
        prep_note, optional = line.prep_note, line.optional

    ingredient_id = None
    method = None
    if match := catalog.match(name):
        ingredient_id = session.scalars(
            select(Ingredient.id).where(Ingredient.canonical_name == match.entry.canonical_name)
        ).one_or_none()
        method = match.method.value if ingredient_id is not None else None
    return RecipeIngredient(
        position=position,
        raw_text=line.raw_text,
        ingredient_id=ingredient_id,
        qty=qty,
        unit=unit,
        prep_note=prep_note,
        optional=optional,
        match_method=method,
    )


def create_draft(
    session: Session, draft: RecipeDraft, catalog: Catalog, ref: str | None = None
) -> Recipe:
    """ING-3: every import lands as a new draft. Never updates an existing recipe (REC-8)."""
    ref = ref or next_ref(session, draft.collection)
    if session.scalars(select(Recipe.id).where(Recipe.ref == ref)).first() is not None:
        raise LibraryError(f"recipe {ref!r} already exists; imports never overwrite (REC-8)")
    recipe = Recipe(
        ref=ref,
        title=draft.title,
        servings=draft.servings,
        prep_minutes=draft.prep_minutes,
        cook_minutes=draft.cook_minutes,
        total_minutes=draft.total_minutes,
        meal_role=draft.meal_role,
        tags=sorted(set(draft.tags)),
        status=RecipeStatus.DRAFT,
        collection=draft.collection,
    )
    recipe.sources = [
        RecipeSource(
            kind=s.kind,
            title=s.title,
            author=s.author,
            url=str(s.url) if s.url else "",
            file=s.file,
            pages=", ".join(str(p) for p in s.pages),
            confidence=s.confidence,
        )
        for s in draft.sources
    ]
    recipe.ingredients = [
        _ingredient_row(session, catalog, line, i) for i, line in enumerate(draft.ingredients, 1)
    ]
    recipe.steps = [
        Step(
            position=i,
            text=s.text,
            equipment=s.equipment,
            active_minutes=s.active_minutes,
            passive_minutes=s.passive_minutes,
        )
        for i, s in enumerate(draft.steps, 1)
    ]
    session.add(recipe)
    session.flush()
    return recipe


def edit_recipe(session: Session, recipe: Recipe, edit: RecipeEdit, catalog: Catalog) -> Recipe:
    """A person's edit (UI-5): replace the fields, ingredients and steps. Ingredient lines are
    parsed and matched again; sources, ratings, family and status are kept."""
    recipe.title = edit.title
    recipe.servings = edit.servings
    recipe.prep_minutes = edit.prep_minutes
    recipe.cook_minutes = edit.cook_minutes
    recipe.total_minutes = edit.total_minutes
    recipe.meal_role = edit.meal_role
    recipe.tags = sorted({t.strip() for t in edit.tags if t.strip()})
    recipe.household_notes = edit.household_notes
    recipe.ingredients.clear()
    recipe.steps.clear()
    session.flush()
    lines = [line.strip() for line in edit.ingredients if line.strip()]
    recipe.ingredients = [
        _ingredient_row(session, catalog, IngredientLine(raw_text=line), i)
        for i, line in enumerate(lines, 1)
    ]
    recipe.steps = [
        Step(
            position=i,
            text=s.text,
            equipment=[e.strip() for e in s.equipment if e.strip()],
            active_minutes=s.active_minutes,
            passive_minutes=s.passive_minutes,
        )
        for i, s in enumerate(edit.steps, 1)
    ]
    session.flush()
    return recipe


# --- copies vs variants (REC-3) -------------------------------------------------------------

_TITLE_NOISE = {"the", "a", "an", "with", "and", "of", "in", "best", "easy", "quick", "authentic"}
_TITLE_NOISE |= {"homemade", "simple", "classic", "recipe", "style"}


def title_tokens(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9']+", title.lower())
    return frozenset(w for w in words if w not in _TITLE_NOISE and w not in DESCRIPTORS)


def _jaccard(a: frozenset[object], b: frozenset[object]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


@dataclass(frozen=True)
class Similar:
    recipe: Recipe
    score: float
    title_score: float
    ingredient_score: float | None


def similar_recipes(session: Session, recipe: Recipe) -> list[Similar]:
    """Candidates for "same recipe" or "same dish": normalized title + ingredient-set overlap."""
    mine_title = title_tokens(recipe.title)
    mine_ings = frozenset(i.ingredient_id for i in recipe.ingredients if i.ingredient_id)
    found = []
    for other in session.scalars(select(Recipe).where(Recipe.id != recipe.id)):
        t = _jaccard(mine_title, title_tokens(other.title))
        theirs = frozenset(i.ingredient_id for i in other.ingredients if i.ingredient_id)
        ing = _jaccard(mine_ings, theirs) if mine_ings and theirs else None
        score = t if ing is None else 0.5 * t + 0.5 * ing
        if score >= SIMILARITY_THRESHOLD:
            found.append(Similar(other, round(score, 3), round(t, 3), ing))
    return sorted(found, key=lambda s: (-s.score, s.recipe.ref))


def merge_copy(session: Session, draft: Recipe, into: Recipe) -> Recipe:
    """REC-3: a second copy of the same recipe collapses into one record keeping all page refs.

    The existing recipe's content is untouched (REC-8); only the draft's sources move over.
    """
    if draft.status is not RecipeStatus.DRAFT:
        raise LibraryError(f"{draft.ref} is not a draft; only drafts can be merged as copies")
    if draft.id == into.id:
        raise LibraryError("cannot merge a recipe into itself")
    # Load both collections before moving anything: a lazy load mid-move would autoflush
    # and delete the moving source as an orphan of the draft.
    with session.no_autoflush:
        moving = list(draft.sources)
        into.sources.extend([])  # loads the target's sources
        for src in moving:
            draft.sources.remove(src)
            into.sources.append(src)
    session.delete(draft)
    session.flush()
    return into


# --- families (REC-9) -----------------------------------------------------------------------


def add_to_family(session: Session, recipe: Recipe, family_name: str) -> RecipeFamily:
    family = session.scalars(
        select(RecipeFamily).where(RecipeFamily.name == family_name)
    ).one_or_none()
    if family is None:
        family = RecipeFamily(name=family_name)
        session.add(family)
    recipe.family = family
    session.flush()
    return family


def mean_rating(session: Session, recipe_id: int) -> float | None:
    avg = session.scalar(select(func.avg(Rating.score)).where(Rating.recipe_id == recipe_id))
    return float(avg) if avg is not None else None


def preferred_variant(session: Session, family: RecipeFamily) -> Recipe:
    """The pinned preferred variant, else the highest rated (ties: lowest ref)."""
    if family.preferred_recipe_id is not None:
        return session.get_one(Recipe, family.preferred_recipe_id)
    if not family.recipes:
        raise LibraryError(f"family {family.name!r} has no recipes")
    return min(family.recipes, key=lambda r: (-(mean_rating(session, r.id) or 0.0), r.ref))


# --- ratings, favorites, promotion (REC-5, REC-7) ------------------------------------------


def rate(
    session: Session,
    recipe: Recipe,
    score: int,
    on: date,
    would_repeat: bool | None = None,
    notes: str = "",
) -> Rating:
    """Record a rating; a discovered recipe rated 4+ after cooking is promoted to core."""
    if not 1 <= score <= 5:
        raise LibraryError("score must be 1-5")
    rating = Rating(
        recipe_id=recipe.id, date=on, score=score, would_repeat=would_repeat, notes=notes
    )
    session.add(rating)
    if recipe.collection is Collection.DISCOVERED and score >= PROMOTION_MIN_SCORE:
        recipe.collection = Collection.CORE
    session.flush()
    return rating


def promote(recipe: Recipe) -> None:
    """REC-7: promote a discovered recipe to core by hand."""
    recipe.collection = Collection.CORE


def favorite_score(session: Session, recipe: Recipe) -> float:
    """REC-5: mean rating, nudged by how often it was rated; pinned recipes always rank first."""
    if recipe.pinned_favorite:
        return 100.0
    ratings = session.scalars(select(Rating).where(Rating.recipe_id == recipe.id)).all()
    if not ratings:
        return 0.0
    mean = sum(r.score for r in ratings) / len(ratings)
    repeats = sum(1 for r in ratings if r.would_repeat)
    return round(mean + 0.1 * min(len(ratings), 5) + 0.1 * min(repeats, 5), 3)


def favorites(session: Session, limit: int = 10) -> list[tuple[Recipe, float]]:
    scored = [
        (r, favorite_score(session, r))
        for r in session.scalars(select(Recipe).where(Recipe.status == RecipeStatus.APPROVED))
    ]
    scored = [(r, s) for r, s in scored if s > 0]
    return sorted(scored, key=lambda rs: (-rs[1], rs[0].ref))[:limit]


# --- household notes as data -----------------------------------------------------------------


@dataclass(frozen=True)
class HouseholdSignals:
    """What handwritten notes imply: a seed rating and tags."""

    rating: int | None = None
    tags: frozenset[str] = field(default_factory=frozenset)


_RATING_WORDS = (
    (re.compile(r"\b(excellent|awesome|amazing|favorite|love)\b", re.I), 5),
    (re.compile(r"\bok\b.*\bnot great\b|\bnot great\b|\bmeh\b", re.I), 2),
    (re.compile(r"\b(very good|great)\b", re.I), 4),
)
_TAG_RULES = (
    (re.compile(r"freezer", re.I), "freezer-friendly"),
    (re.compile(r"\bx\s*2\b|double recipe", re.I), "double-batch"),
    (re.compile(r"make[- ]ahead|prep[- ]ahead", re.I), "make-ahead"),
    (re.compile(r"\blunch\b", re.I), "lunch-friendly"),
)


def household_signals(notes: str) -> HouseholdSignals:
    """Handwriting is data: "Excellent" -> 5, "OK, not great" -> 2, "x2 (freezer)" -> tags."""
    rating = None
    if "handwritten" in notes.lower() or "rating" in notes.lower():
        for pattern, score in _RATING_WORDS:
            if pattern.search(notes):
                rating = score
                break
    tags = frozenset(tag for pattern, tag in _TAG_RULES if pattern.search(notes))
    return HouseholdSignals(rating, tags)
