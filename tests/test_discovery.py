"""M5: the check every web find goes through (REC-7, REC-8, §5 dislike guardrail)."""

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mealplan.core import discovery, library, recipe_facts
from mealplan.core.discovery import Candidate, DiscoveryError, add_candidate, check_page, check_urls
from mealplan.core.preferences import HouseholdPrefs
from mealplan.ingest.fetch import FetchError, Page
from mealplan.models.enums import Collection, RecipeStatus
from tests.planning_fixtures import populate_golden

PAGES = Path(__file__).parent / "golden" / "url" / "pages"


def page(name: str, url: str | None = None) -> Page:
    url = url or f"https://recipes.example/{name}"
    return Page(url, url, (PAGES / f"{name}.html").read_text(encoding="utf-8"))


def recipe_page(url: str, title: str, lines: list[str], steps: list[str] | None = None) -> Page:
    data = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": title,
        "recipeYield": "4",
        "recipeIngredient": lines,
        "recipeInstructions": steps or ["Cook it."],
    }
    html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
    return Page(url, url, html)


@pytest.fixture
def lib(session, catalog):
    populate_golden(session, catalog)
    return session


def test_a_new_soup_is_ok_with_facts(lib, catalog):
    c = check_page(lib, HouseholdPrefs(), catalog, page("plain"))
    assert (c.verdict, c.title, c.site) == ("ok", "Red Lentil Soup", "Good Bowls")
    assert c.facts["role"] == "soup" and c.facts["protein"] == "vegetarian"
    assert c.facts["ingredients"] == 6 and c.facts["steps"] == 3
    assert c.draft is not None and c.draft.collection is Collection.DISCOVERED


def test_avoided_ingredient_blocks_with_the_reason(lib, catalog):
    prefs = HouseholdPrefs(avoid_ingredients=["cumin"])
    c = check_page(lib, prefs, catalog, page("plain"))
    assert c.verdict == "blocked" and c.reasons == ["contains cumin"]


def test_too_spicy_is_blocked(lib, catalog):
    hot = recipe_page(
        "https://hot.example/w",
        "Fiery Pepper Wings",
        ["2 lb chicken wings", "10 habanero peppers, minced", "1/4 cup cayenne pepper"],
    )
    c = check_page(lib, HouseholdPrefs(), catalog, hot)
    assert c.verdict == "blocked" and c.reasons[0].startswith("too spicy")


def test_an_unmatched_line_naming_an_avoided_food_still_blocks(lib, catalog):
    odd = recipe_page("https://x.example/a", "Odd Salad", ["a handful of zorbleberries, torn"])
    c = check_page(lib, HouseholdPrefs(avoid_ingredients=["zorbleberries"]), catalog, odd)
    assert c.verdict == "blocked" and c.reasons == ["contains zorbleberries"]


def test_a_copy_of_a_core_recipe_is_a_duplicate(lib, catalog):
    copy = recipe_page(
        "https://blog.example/shakshuka",
        "Easy Shakshuka",
        [
            "2 tablespoons olive oil",
            "1 large yellow onion, chopped",
            "2 green peppers, chopped",
            "1 (28-ounce) can crushed tomatoes",
            "6 large eggs",
        ],
    )
    c = check_page(lib, HouseholdPrefs(), catalog, copy)
    assert c.verdict == "duplicate"
    assert c.similar[0][0] == "core-024" and "core-024" in c.reasons[0]


def test_unreadable_and_fetch_errors(lib, catalog):
    def fetcher(url: str) -> Page:
        if "down" in url:
            raise FetchError("the site answered 503")
        return page("no_jsonld", url)

    got = check_urls(
        lib,
        HouseholdPrefs(),
        catalog,
        ["https://down.example/x", "https://blog.example/stew"],
        fetcher=fetcher,
    )
    assert [(c.verdict, c.reasons) for c in got] == [
        ("unreadable", ["the site answered 503"]),
        ("unreadable", ["no structured recipe on this page"]),
    ]


def test_check_urls_keeps_order_drops_repeats_and_carries_notes(lib, catalog):
    urls = ["https://a.example/1", " https://a.example/1 ", "https://b.example/2", ""]
    got = check_urls(
        lib,
        HouseholdPrefs(),
        catalog,
        urls,
        fetcher=lambda u: page("plain" if "a." in u else "graph", u),
        notes={"https://b.example/2": "a packable lunch"},
    )
    assert [c.url for c in got] == ["https://a.example/1", "https://b.example/2"]
    assert got[1].note == "a packable lunch" and got[1].facts["role"] == "lunch"


def test_added_recipes_are_discovered_drafts_and_then_known(lib, catalog):
    c = check_page(lib, HouseholdPrefs(), catalog, page("plain", "https://www.good.example/soup/"))
    recipe = add_candidate(lib, catalog, c)
    assert recipe.status is RecipeStatus.DRAFT and recipe.collection is Collection.DISCOVERED
    assert recipe.sources[0].url == "https://www.good.example/soup/"
    again = check_page(lib, HouseholdPrefs(), catalog, page("plain", "http://good.example/soup"))
    assert again.verdict == "exists"


def test_a_duplicate_can_be_added_as_a_variant(lib, catalog):
    copy = recipe_page(
        "https://blog.example/shakshuka",
        "Easy Shakshuka",
        ["2 tablespoons olive oil", "6 large eggs", "1 (28-ounce) can crushed tomatoes"],
    )
    c = check_page(lib, HouseholdPrefs(), catalog, copy)
    recipe = add_candidate(lib, catalog, c, variant_of="core-024")
    assert recipe.family is not None
    assert recipe.family is library.get_recipe(lib, "core-024").family


def test_a_variant_of_a_recipe_without_a_family_starts_one(lib, catalog):
    c = check_page(lib, HouseholdPrefs(), catalog, page("plain"))
    recipe = add_candidate(lib, catalog, c, variant_of="core-023")
    target = library.get_recipe(lib, "core-023")
    assert target.family is not None and recipe.family is target.family


def test_blocked_or_unreadable_cannot_be_added(lib, catalog):
    with pytest.raises(DiscoveryError):
        add_candidate(lib, catalog, Candidate("https://x.example", "unreadable", reasons=["no"]))


@settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    avoid=st.lists(
        st.sampled_from(["onion", "carrot", "cumin", "chicken thigh", "tahini", "beef", "egg"]),
        max_size=3,
    ),
    max_spice=st.integers(0, 3),
    name=st.sampled_from(["plain", "graph", "sections", "string_steps", "broken_then_valid"]),
)
def test_an_ok_find_never_breaks_a_household_rule(lib, catalog, avoid, max_spice, name):
    """§5 guardrail: whatever the pages say, an `ok` or `duplicate` verdict has no violation."""
    prefs = HouseholdPrefs(avoid_ingredients=avoid, max_spice=max_spice)
    c = check_page(lib, prefs, catalog, page(name))
    if c.verdict in ("ok", "duplicate"):
        assert c.draft is not None
        dish = discovery.dish_from_draft(c.draft, catalog)
        assert not recipe_facts.violations(dish, prefs)
        assert not {i.name for i in dish.ingredients} & set(avoid)
        assert recipe_facts.spice_level(dish) <= max_spice
