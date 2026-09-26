"""Shopping list engine (SHP-1..4): the week's needs, netted against inventory.

1. Gather needs: planned dinners scaled to their servings, prep-day lunch batches, make-ahead
   dinners (once). Leftover lunches add nothing; order-in nights add nothing.
2. One line per ingredient and unit: the catalog's default unit when the amounts convert to
   it, else ounces for weights and cups for volumes; counts stay counts.
3. Subtract what inventory holds (converted to the line's unit) and show the math.
4. Round up to pack sizes; group by store section in walking order.
Staples are assumed on hand unless marked out. Nothing is dropped silently: unmatched lines
and missing amounts stay on the list with a note.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from mealplan.core.base_week import ORDER_IN_TAG
from mealplan.core.normalizer import Catalog, CatalogEntry
from mealplan.core.planner import ComponentUse, PlannedMeal, WeekPlanResult
from mealplan.core.prep import batch_servings
from mealplan.core.recipe_facts import Dish, DishIngredient
from mealplan.core.units import EACH, UNITS, Dimension, try_convert
from mealplan.models.enums import Meal

PACKAGE_UNITS = {"can", "jar", "package", "packet", "bag", "box", "bottle"}
EPS = 1e-9


@dataclass(frozen=True)
class Need:
    ingredient: DishIngredient
    source: str  # what it is for: a recipe or component name


def _scaled(ingredients: tuple[DishIngredient, ...], factor: float, source: str) -> list[Need]:
    return [
        Need(
            DishIngredient(
                i.name,
                i.qty * factor if i.qty is not None else None,
                i.unit,
                i.optional,
                i.matched,
            ),
            source,
        )
        for i in ingredients
    ]


def dinner_needs(meal: PlannedMeal, dish: Dish) -> list[Need]:
    if ORDER_IN_TAG in dish.tags:
        return []
    factor = meal.servings / dish.servings if dish.servings else 1.0
    return _scaled(dish.ingredients, factor, dish.title)


def component_needs(use: ComponentUse, dishes: dict[str, Dish]) -> list[Need]:
    spec = use.spec
    if spec.recipe_ref is None:
        return _scaled(spec.per_serving, use.servings, spec.name)
    dish = dishes.get(spec.recipe_ref)
    if dish is None:
        return []
    batch = batch_servings(spec, dish, use.servings)
    factor = batch / dish.servings if dish.servings else 1.0
    return _scaled(dish.ingredients, factor, dish.title)


def week_needs(result: WeekPlanResult, dishes: dict[str, Dish]) -> list[Need]:
    needs: list[Need] = []
    for meal in result.meals:
        if meal.meal is Meal.DINNER and meal.ref and meal.ref in dishes:
            needs += dinner_needs(meal, dishes[meal.ref])
    for use in result.components:
        needs += component_needs(use, dishes)
    return needs


@dataclass(frozen=True)
class ListLine:
    name: str
    section: str
    unit: str
    needed: float
    on_hand: float = 0.0
    to_buy: float = 0.0
    packs: int | None = None
    pack_size: float | None = None
    pack_unit: str | None = None
    ingredient: str | None = None  # catalog name; None when unmatched
    notes: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShoppingListResult:
    week_start: date
    lines: tuple[ListLine, ...]  # to buy, in store order
    have: tuple[ListLine, ...]  # needed but fully on hand
    staples: tuple[ListLine, ...]  # assumed on hand
    checks: tuple[str, ...] = ()


def line_unit(unit: str | None, entry: CatalogEntry | None) -> str:
    if unit is None or unit == EACH:
        return EACH
    dim = UNITS[unit][0]
    if dim is Dimension.COUNT:
        return unit
    if (
        entry is not None
        and entry.default_unit is not None
        and try_convert(1.0, unit, entry.default_unit, entry.density_g_per_ml) is not None
    ):
        return entry.default_unit
    return "oz" if dim is Dimension.MASS else "cup"


@dataclass
class _Acc:
    qty: float = 0.0
    missing_qty: bool = False
    optional_only: bool = True
    uses: set[str] = field(default_factory=set)


def _packs(
    to_buy: float, unit: str, entry: CatalogEntry | None
) -> tuple[int | None, float | None, str | None]:
    if to_buy <= EPS:
        return None, None, None
    if unit in PACKAGE_UNITS:  # "2 cans": buy the cans; show their size if known
        if entry is not None and entry.pack_unit:
            return math.ceil(to_buy - EPS), entry.pack_size, entry.pack_unit
        return math.ceil(to_buy - EPS), None, None
    if entry is None or entry.pack_size is None:
        return None, None, None
    pack_unit = entry.pack_unit or EACH
    in_pack_unit = try_convert(to_buy, unit, pack_unit, entry.density_g_per_ml)
    if in_pack_unit is None:
        return None, None, None
    return math.ceil(in_pack_unit / entry.pack_size - EPS), entry.pack_size, entry.pack_unit


def build_list(
    week_start: date,
    needs: list[Need],
    catalog: Catalog,
    on_hand: dict[str, list[tuple[float, str]]],
    staples_out: set[str],
    layout: list[str],
) -> ShoppingListResult:
    acc: dict[tuple[str, str], _Acc] = defaultdict(_Acc)
    matched: dict[str, CatalogEntry] = {}
    staples: dict[str, set[str]] = defaultdict(set)
    checks: list[str] = []

    for need in needs:
        ing = need.ingredient
        entry = None
        if ing.matched:
            try:
                entry = catalog.get(ing.name)
            except KeyError:
                entry = None
        if entry is not None and entry.staple and entry.canonical_name not in staples_out:
            staples[entry.canonical_name].add(need.source)
            continue
        name = entry.canonical_name if entry is not None else ing.name
        if entry is not None:
            matched[name] = entry
        unit = line_unit(ing.unit, entry) if ing.unit in UNITS or ing.unit is None else EACH
        a = acc[(name, unit)]
        a.uses.add(need.source)
        a.optional_only = a.optional_only and ing.optional
        if ing.qty is None:
            a.missing_qty = True
            continue
        density = entry.density_g_per_ml if entry is not None else None
        converted = try_convert(ing.qty, ing.unit, unit, density)
        a.qty += converted if converted is not None else ing.qty

    units_by_name: dict[str, list[str]] = defaultdict(list)
    for name, unit in acc:
        units_by_name[name].append(unit)

    buy: list[ListLine] = []
    have: list[ListLine] = []
    for (name, unit), a in sorted(acc.items()):
        entry = matched.get(name)
        notes: list[str] = []
        if entry is None:
            notes.append("not matched to the catalog: check it")
            checks.append(f"Unmatched ingredient on the list: {name!r}")
        if a.missing_qty:
            notes.append("amount not given in a recipe: check it")
        if a.optional_only:
            notes.append("optional")
        if name in staples_out:
            notes.append("staple marked out")
        others = [u for u in units_by_name[name] if u != unit]
        if others:
            notes.append(f"also listed in {', '.join(sorted(others))}")
            checks.append(f"{name} is needed in units that do not convert: check both lines")

        density = entry.density_g_per_ml if entry is not None else None
        held = 0.0
        for qty, held_unit in on_hand.get(name, []):
            converted = try_convert(qty, held_unit, unit, density)
            if converted is None:
                notes.append(f"have {qty:g} {held_unit} that does not convert: check")
            else:
                held += converted
        needed = round(a.qty, 4)
        on_hand_qty = round(min(held, needed), 4) if needed else round(held, 4)
        to_buy = round(max(needed - held, 0.0), 4)
        if unit == EACH or UNITS.get(unit, (Dimension.MASS, None))[0] is Dimension.COUNT:
            to_buy = float(math.ceil(to_buy - EPS))
        packs, pack_size, pack_unit = _packs(to_buy, unit, entry)
        if name in staples_out:  # a staple that ran out is bought as one container
            packs, pack_size, pack_unit = 1, None, None
        line = ListLine(
            name=name,
            section=entry.section if entry is not None else "other",
            unit=unit,
            needed=needed,
            on_hand=on_hand_qty,
            to_buy=to_buy,
            packs=packs,
            pack_size=pack_size,
            pack_unit=pack_unit,
            ingredient=entry.canonical_name if entry is not None else None,
            notes=tuple(notes),
            uses=tuple(sorted(a.uses)),
        )
        if to_buy <= EPS and needed > 0 and not a.missing_qty:
            have.append(line)
        else:
            buy.append(line)

    order = {section: i for i, section in enumerate(layout)}

    def store_order(line: ListLine) -> tuple[int, str, str, str]:
        return (order.get(line.section, len(order)), line.section, line.name, line.unit)

    staple_lines = tuple(
        ListLine(
            name=n, section="staples", unit=EACH, needed=0, ingredient=n, uses=tuple(sorted(u))
        )
        for n, u in sorted(staples.items())
    )
    return ShoppingListResult(
        week_start=week_start,
        lines=tuple(sorted(buy, key=store_order)),
        have=tuple(sorted(have, key=store_order)),
        staples=staple_lines,
        checks=tuple(dict.fromkeys(checks)),
    )
