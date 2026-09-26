"""NFR-1 golden week: plan, prep checklist and day cards must match byte-for-byte.

After an intended change, regenerate with `UPDATE_GOLDEN=1 uv run pytest tests/test_golden_week.py`
and review the diff before committing.
"""

import os
import random
import time
from dataclasses import replace

import pytest

from mealplan.core.normalizer import Catalog
from mealplan.core.planner import plan_week
from mealplan.core.preferences import HouseholdPrefs
from mealplan.core.prep import build_prep
from mealplan.core.recipe_facts import Dish
from mealplan.core.render import day_cards_markdown, plan_markdown, prep_markdown
from mealplan.core.render_list import shopping_markdown
from mealplan.core.shopping import build_list, week_needs
from mealplan.models.enums import Collection
from tests.planning_fixtures import (
    GOLDEN_ON_HAND,
    GOLDEN_WEEK,
    golden_inputs,
    golden_seed,
    golden_start,
)


@pytest.fixture(scope="module")
def rendered(catalog: Catalog) -> dict[str, str]:
    inputs = golden_inputs(catalog)
    plan = plan_week(inputs, HouseholdPrefs(), golden_start(), golden_seed())
    dishes = {d.ref: d for d in inputs.dishes}
    prep = build_prep(plan, dishes)
    shopping = build_list(
        plan.week_start,
        week_needs(plan, dishes),
        catalog,
        GOLDEN_ON_HAND,
        set(),
        HouseholdPrefs().store_layout,
    )
    return {
        "plan.md": plan_markdown(plan),
        "prep.md": prep_markdown(prep),
        "daycards.md": day_cards_markdown(plan, prep, dishes),
        "shopping.md": shopping_markdown(shopping),
    }


@pytest.mark.parametrize("name", ["plan.md", "prep.md", "daycards.md", "shopping.md"])
def test_matches_golden_file(rendered, name):
    path = GOLDEN_WEEK / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(rendered[name], encoding="utf-8")
    assert rendered[name] == path.read_text(encoding="utf-8")


def test_lines_fit_a_phone(rendered):
    """NFR-9: day cards stay readable without horizontal scrolling."""
    for line in rendered["daycards.md"].splitlines():
        assert len(line) <= 110, line


def test_plan_and_prep_for_a_large_library_is_fast(catalog):
    """NFR-3: 7-day plan plus prep in under 2 s for 100+ recipes."""
    base = golden_inputs(catalog)
    rng = random.Random(1)
    extra = tuple(
        replace(
            rng.choice(base.dishes),
            ref=f"core-{500 + i}",
            title=f"Variant {i}",
            family=None,
            collection=Collection.CORE,
            favorite=rng.choice([None, 3.1, 4.2, 5.1]),
        )
        for i in range(120)
    )
    big = replace(base, dishes=base.dishes + extra)
    start = time.perf_counter()
    plan = plan_week(big, HouseholdPrefs(), golden_start(), 3)
    dishes: dict[str, Dish] = {d.ref: d for d in big.dishes}
    build_prep(plan, dishes)
    assert time.perf_counter() - start < 2.0
