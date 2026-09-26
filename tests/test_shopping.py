"""Shopping list engine against a hand-computed week (SHP-1..4, M3 acceptance)."""

from datetime import date

import pytest

from mealplan.core.components import ComponentSpec
from mealplan.core.planner import ComponentUse, PlannedMeal, WeekPlanResult
from mealplan.core.preferences import HouseholdPrefs
from mealplan.core.recipe_facts import Dish, DishIngredient
from mealplan.core.shopping import ListLine, ShoppingListResult, build_list, week_needs
from mealplan.models.enums import Meal, MealRole

SUN = date(2026, 10, 4)
Ing = DishIngredient

TACOS = Dish(
    "house-003",
    "Tacos (Meat)",
    MealRole.DINNER,
    servings=4,
    ingredients=(
        Ing("ground beef", 1.5, "lb"),
        Ing("taco seasoning", 2, "tbsp"),
        Ing("tortilla", 12, None),
        Ing("tomato", 2, None),
        Ing("sour cream", 1, "cup"),
        Ing("monterey jack", 2, "cup"),
        Ing("avocado", 2, None),
    ),
)
CHILI = Dish(
    "core-900",
    "Chili",
    MealRole.DINNER,
    servings=4,
    ingredients=(
        Ing("ground beef", 1, "lb"),
        Ing("black beans", 2, "can"),
        Ing("onion", 1, None),
        Ing("chili powder", 1, "tbsp"),  # staple
        Ing("sour cream", 0.5, "cup"),
        Ing("cilantro", None, None, optional=True),
        Ing("1 bag of mystery chips", 1, "bag", matched=False),
    ),
)
PIZZA = Dish("house-004", "Pizza Night", MealRole.DINNER, servings=4, tags=frozenset({"order-in"}))
GREENS = ComponentSpec(
    "washed salad greens", "salad-base", 5, False, 10, 0, (), (Ing("mixed greens", 1.5, "cup"),)
)

PLAN = WeekPlanResult(
    week_start=SUN,
    seed=1,
    meals=(
        PlannedMeal(date(2026, 10, 6), Meal.DINNER, "house-003", "Tacos (Meat)", 6),
        PlannedMeal(
            date(2026, 10, 7), Meal.LUNCH, "house-003", "Tacos (Meat)", 2, date(2026, 10, 6)
        ),
        PlannedMeal(date(2026, 10, 8), Meal.DINNER, "core-900", "Chili", 4),
        PlannedMeal(date(2026, 10, 9), Meal.DINNER, "house-004", "Pizza Night", 4),
    ),
    components=(ComponentUse(GREENS, (date(2026, 10, 5),), 2),),
    make_ahead=(),
    conflicts=(),
)
DISHES = {d.ref: d for d in (TACOS, CHILI, PIZZA)}
ON_HAND = {"ground beef": [(1.0, "lb")], "sour cream": [(8.0, "oz")]}


@pytest.fixture(scope="module")
def shopping(catalog):
    needs = week_needs(PLAN, DISHES)
    return build_list(SUN, needs, catalog, ON_HAND, set(), HouseholdPrefs().store_layout)


def line(result: ShoppingListResult, name: str) -> ListLine:
    matches = [ln for ln in (*result.lines, *result.have) if ln.name == name]
    assert len(matches) == 1, (name, matches)
    return matches[0]


def test_leftover_lunch_and_order_in_add_nothing():
    sources = {n.source for n in week_needs(PLAN, DISHES)}
    assert sources == {"Tacos (Meat)", "Chili", "washed salad greens"}


def test_hand_computed_lines(shopping):
    # ground beef: tacos 1.5 lb x 6/4 = 2.25 + chili 1 = 3.25 lb; have 1 -> buy 2.25
    # in 1 lb packs -> 3
    beef = line(shopping, "ground beef")
    assert (beef.unit, beef.needed, beef.on_hand, beef.to_buy) == ("lb", 3.25, 1.0, 2.25)
    assert (beef.packs, beef.pack_size, beef.pack_unit) == (3, 1.0, "lb")
    assert beef.uses == ("Chili", "Tacos (Meat)")

    # tortillas: 12 x 1.5 = 18, no pack size in the catalog
    tortilla = line(shopping, "tortilla")
    assert (tortilla.unit, tortilla.to_buy, tortilla.packs) == ("each", 18, None)

    # tomatoes 2 x 1.5 = 3; avocados 2 x 1.5 = 3; onion 1
    assert line(shopping, "tomato").to_buy == 3
    assert line(shopping, "avocado").to_buy == 3
    assert line(shopping, "onion").to_buy == 1

    # sour cream: 1 x 1.5 + 0.5 = 2 cups; have 8 oz by weight = 226.8 g / 1.0 g/ml = 0.9586 cup
    # -> buy 1.0414 cup = 246.4 g = 8.69 oz; 16 oz tubs -> 1
    sour = line(shopping, "sour cream")
    assert sour.unit == "cup" and sour.needed == 2.0
    assert sour.on_hand == pytest.approx(0.9586, abs=1e-3)
    assert sour.to_buy == pytest.approx(1.0414, abs=1e-3)
    assert (sour.packs, sour.pack_size, sour.pack_unit) == (1, 16.0, "oz")

    # shredded cheese: 2 cups x 1.5 = 3 cups -> default unit oz at 0.45 g/ml:
    # 3 cups = 709.76 ml x 0.45 = 319.4 g = 11.27 oz; 8 oz bags -> 2
    cheese = line(shopping, "monterey jack")
    assert cheese.unit == "oz"
    assert cheese.to_buy == pytest.approx(11.27, abs=0.01)
    assert cheese.packs == 2

    # black beans: 2 cans stay cans; taco seasoning 2 tbsp x 1.5 = 3 tbsp
    beans = line(shopping, "black beans")
    assert (beans.unit, beans.to_buy, beans.packs, beans.pack_size) == ("can", 2, 2, 15.0)
    assert line(shopping, "taco seasoning").needed == 3

    # greens for Monday lunch: 1.5 cups x 2 servings = 3 cups; 5 oz clamshells do not convert
    greens = line(shopping, "mixed greens")
    assert (greens.unit, greens.to_buy, greens.packs) == ("cup", 3, None)


def test_staples_optional_unmatched(shopping):
    assert [s.name for s in shopping.staples] == ["chili powder"]
    cilantro = line(shopping, "cilantro")
    assert (
        "optional" in cilantro.notes and "amount not given in a recipe: check it" in cilantro.notes
    )
    chips = line(shopping, "1 bag of mystery chips")
    assert chips.section == "other" and chips.ingredient is None
    assert any("mystery chips" in c for c in shopping.checks)


def test_store_order(shopping):
    sections = [ln.section for ln in shopping.lines]
    layout = HouseholdPrefs().store_layout
    known = [s for s in sections if s in layout]
    assert known == sorted(known, key=layout.index)
    assert sections[-1] == "other"
    assert sections[0] == "produce"


def test_fully_stocked_items_move_to_have(catalog):
    stocked = {**ON_HAND, "onion": [(3.0, "each")]}
    result = build_list(SUN, week_needs(PLAN, DISHES), catalog, stocked, set(), [])
    assert [ln.name for ln in result.have] == ["onion"]
    assert result.have[0].on_hand == 1 and result.have[0].to_buy == 0


def test_staple_marked_out_goes_on_the_list(catalog):
    result = build_list(SUN, week_needs(PLAN, DISHES), catalog, ON_HAND, {"chili powder"}, [])
    chili = line(result, "chili powder")
    assert "staple marked out" in chili.notes
    assert (chili.unit, chili.to_buy, chili.packs) == ("tsp", 3, 1)  # buy one jar
    assert result.staples == ()


def test_inventory_in_units_that_do_not_convert_is_flagged(catalog):
    odd = {"black beans": [(2.0, "cup")]}
    result = build_list(SUN, week_needs(PLAN, DISHES), catalog, odd, set(), [])
    beans = line(result, "black beans")
    assert beans.to_buy == 2
    assert any("does not convert" in n for n in beans.notes)
