"""Recipe library and review queue: REC-3, REC-5, REC-7, REC-8, REC-9, ING-3, ING-4."""

from datetime import date

import pytest

from mealplan.core import library
from mealplan.core.library import LibraryError, household_signals
from mealplan.ingest import review_queue
from mealplan.models.enums import Collection, RecipeStatus, SourceKind
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef, StepDraft

PDF = "Recipes_12Sept26.pdf"


def draft(
    title: str,
    lines: list[str],
    pages: list[int] | None = None,
    collection: Collection = Collection.CORE,
) -> RecipeDraft:
    return RecipeDraft(
        title=title,
        servings=4,
        collection=collection,
        ingredients=[IngredientLine(raw_text=line) for line in lines],
        steps=[StepDraft(text="Cook it.", active_minutes=10)],
        sources=[SourceRef(kind=SourceKind.PDF, file=PDF, pages=pages or [1])],
    )


INJERA_A = ["2 cups teff flour", "3 cups water", "1/2 teaspoon salt"]
INJERA_B = ["1 cup teff flour", "1 cup all-purpose flour", "2 1/2 cups water", "1 tsp salt"]
SLOPPY = ["1 lb ground beef", "1 onion, diced", "1 cup ketchup", "2 tbsp brown sugar"]


def test_import_lands_as_draft_with_parsed_and_matched_lines(session, catalog):
    r = library.create_draft(session, draft("Authentic Injera", INJERA_A, [4, 5, 6]), catalog)
    assert r.ref == "core-001"
    assert r.status is RecipeStatus.DRAFT
    assert r.sources[0].pages == "4, 5, 6"
    first = r.ingredients[0]
    assert (first.qty, first.unit, first.raw_text) == (2, "cup", "2 cups teff flour")
    assert first.ingredient_id is not None and first.match_method == "exact"


def test_refs_are_sequential_per_collection(session, catalog):
    library.create_draft(session, draft("A", ["1 egg"]), catalog)
    library.create_draft(session, draft("B", ["1 egg"]), catalog, ref="core-041")
    c = library.create_draft(session, draft("C", ["1 egg"]), catalog)
    d = library.create_draft(
        session, draft("D", ["1 egg"], collection=Collection.DISCOVERED), catalog
    )
    assert (c.ref, d.ref) == ("core-042", "disc-001")


def test_import_never_overwrites_an_existing_recipe(session, catalog):
    library.create_draft(session, draft("Doro Wat", ["2 lb chicken thighs"]), catalog, "core-001")
    with pytest.raises(LibraryError, match="REC-8"):
        library.create_draft(session, draft("Other", ["1 egg"]), catalog, "core-001")


def test_copy_collapses_into_one_record_keeping_every_page(session, catalog):
    """REC-3: Sloppy Joes saved several times becomes one recipe with all page refs."""
    original = library.create_draft(
        session, draft("Slow Cooker Sunday Sloppy Joes", SLOPPY, [97, 98]), catalog
    )
    review_queue.approve(session, original)
    copy = library.create_draft(
        session, draft("Slow Cooker Sunday Sloppy Joes", SLOPPY, [135]), catalog
    )

    item = review_queue.review(session, copy)
    assert item.similar[0].recipe is original
    assert item.similar[0].score == 1.0

    merged = library.merge_copy(session, copy, original)
    assert [s.pages for s in merged.sources] == ["97, 98", "135"]
    assert merged.status is RecipeStatus.APPROVED
    assert [i.raw_text for i in merged.ingredients] == SLOPPY  # content untouched (REC-8)


def test_variants_are_similar_but_stay_separate_in_a_family(session, catalog):
    a = library.create_draft(session, draft("Authentic Injera", INJERA_A), catalog)
    b = library.create_draft(session, draft("Injera", INJERA_B), catalog)
    similar = library.similar_recipes(session, b)
    assert [s.recipe.ref for s in similar] == [a.ref]
    assert similar[0].score < 1.0

    fam = library.add_to_family(session, a, "injera")
    assert library.add_to_family(session, b, "injera") is fam
    assert {r.ref for r in fam.recipes} == {a.ref, b.ref}


def test_unrelated_recipes_are_not_similar(session, catalog):
    library.create_draft(session, draft("Authentic Injera", INJERA_A), catalog)
    s = library.create_draft(session, draft("Sloppy Joes", SLOPPY), catalog)
    assert library.similar_recipes(session, s) == []


def test_preferred_variant_is_pinned_else_highest_rated(session, catalog):
    a = library.create_draft(session, draft("Authentic Injera", INJERA_A), catalog)
    b = library.create_draft(session, draft("Injera", INJERA_B), catalog)
    fam = library.add_to_family(session, a, "injera")
    library.add_to_family(session, b, "injera")
    assert library.preferred_variant(session, fam) is a  # no ratings: lowest ref
    library.rate(session, b, 5, date(2026, 9, 20))
    library.rate(session, a, 3, date(2026, 9, 21))
    assert library.preferred_variant(session, fam) is b
    fam.preferred_recipe_id = a.id
    assert library.preferred_variant(session, fam) is a


def test_discovered_recipe_rated_4_plus_is_promoted(session, catalog):
    r = library.create_draft(
        session,
        draft("Squash Soup", ["1 butternut squash"], collection=Collection.DISCOVERED),
        catalog,
    )
    library.rate(session, r, 3, date(2026, 9, 20))
    assert r.collection is Collection.DISCOVERED
    library.rate(session, r, 4, date(2026, 9, 27))
    assert library.get_recipe(session, r.ref).collection is Collection.CORE


def test_rating_bounds(session, catalog):
    r = library.create_draft(session, draft("X", ["1 egg"]), catalog)
    with pytest.raises(LibraryError):
        library.rate(session, r, 6, date(2026, 9, 20))


def test_favorites_rank_pinned_then_by_ratings(session, catalog):
    recipes = []
    for title in ("A", "B", "C"):
        r = library.create_draft(session, draft(title, ["1 egg"]), catalog)
        review_queue.approve(session, r)
        recipes.append(r)
    a, b, c = recipes
    library.rate(session, a, 4, date(2026, 9, 1))
    library.rate(session, b, 5, date(2026, 9, 1), would_repeat=True)
    c.pinned_favorite = True
    assert [r.ref for r, _ in library.favorites(session)] == [c.ref, b.ref, a.ref]


def test_review_flags_unmatched_and_missing_quantities(session, catalog):
    r = library.create_draft(
        session,
        draft("Mystery", ["2 cups unobtainium", "Salt and pepper to taste", "Kosher salt"]),
        catalog,
    )
    problems = review_queue.issues(r)
    assert "unmatched ingredient: '2 cups unobtainium'" in problems
    assert "unmatched ingredient: 'Salt and pepper to taste'" in problems
    assert "no quantity: 'Kosher salt'" in problems
    assert not any("to taste" in p and "no quantity" in p for p in problems)


def test_approve_and_reject(session, catalog):
    a = library.create_draft(session, draft("A", ["1 egg"]), catalog)
    b = library.create_draft(session, draft("B", ["1 egg"]), catalog)
    assert [r.ref for r in review_queue.pending(session)] == [a.ref, b.ref]
    review_queue.approve(session, a)
    review_queue.reject(session, b)
    assert review_queue.pending(session) == []
    with pytest.raises(LibraryError):
        review_queue.approve(session, a)
    with pytest.raises(LibraryError):
        review_queue.reject(session, a)
    with pytest.raises(LibraryError):
        library.get_recipe(session, b.ref)


def test_approved_recipe_cannot_be_merged_away(session, catalog):
    a = library.create_draft(session, draft("A", ["1 egg"]), catalog)
    b = library.create_draft(session, draft("A", ["1 egg"]), catalog)
    review_queue.approve(session, a)
    with pytest.raises(LibraryError, match="not a draft"):
        library.merge_copy(session, a, b)


@pytest.mark.parametrize(
    ("notes", "rating", "tags"),
    [
        ("Handwritten: Excellent", 5, set()),
        ("Handwritten: Awesome", 5, set()),
        ("Handwritten: OK, not great", 2, set()),
        ("Handwritten: x2 (freezer)", None, {"freezer-friendly", "double-batch"}),
        ("Freezer meal #3; handwritten list", None, {"freezer-friendly"}),
        ("Lunch prep-ahead candidate", None, {"lunch-friendly", "make-ahead"}),
        ("Make ahead 1 day", None, {"make-ahead"}),
        ("Handwritten shopping list", None, set()),
        ("Handwritten: look at NYT for modifications", None, set()),
        ("", None, set()),
    ],
)
def test_household_signals(notes, rating, tags):
    s = household_signals(notes)
    assert s.rating == rating
    assert s.tags == tags
