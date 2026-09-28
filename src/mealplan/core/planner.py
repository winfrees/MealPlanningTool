"""Weekly planner: dinners, leftover lunches, and template lunches (PLN-1..4, 7, 8; REC-9).

Scored greedy search with a seed (ADR-0005). `plan_week` is a pure function of its inputs:
the same library, history, preferences, and seed always give the same plan (NFR-1).
"""

import datetime as dt
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from mealplan.core import recipe_facts
from mealplan.core.base_week import ORDER_IN_TAG, pick_rotation, rotation
from mealplan.core.components import TEMPLATES, ComponentSpec, from_dish
from mealplan.core.preferences import HouseholdPrefs, Weekday
from mealplan.core.recipe_facts import Dish
from mealplan.models.enums import Collection, Meal, MealRole

NEUTRAL_FAVORITE = 3.0  # unrated recipes score like an average one
EXPIRING_CAP = 2


@dataclass(frozen=True)
class PlanInputs:
    dishes: tuple[Dish, ...]  # approved recipes
    components: tuple[ComponentSpec, ...]  # generic prep components
    history: dict[str, date] = field(default_factory=dict)  # ref -> last planned before the week
    expiring: frozenset[str] = frozenset()  # ingredient names near their best-by date
    locked: dict[tuple[date, Meal], str] = field(default_factory=dict)  # manual overrides


@dataclass(frozen=True)
class PlannedMeal:
    date: dt.date
    meal: Meal
    ref: str | None
    title: str
    servings: float
    leftover_of: dt.date | None = None  # the dinner this lunch is left over from
    components: tuple[str, ...] = ()  # template lunch parts
    template: str | None = None
    locked: bool = False


@dataclass(frozen=True)
class ComponentUse:
    spec: ComponentSpec
    days: tuple[date, ...]
    servings: float


@dataclass(frozen=True)
class WeekPlanResult:
    week_start: date
    seed: int
    meals: tuple[PlannedMeal, ...]
    components: tuple[ComponentUse, ...]
    make_ahead: tuple[str, ...]  # dinner refs cooked on prep day
    conflicts: tuple[str, ...]


def _label(day: date) -> str:
    return day.strftime("%a %d %b")


def _allowed(dish: Dish, prefs: HouseholdPrefs) -> bool:
    """Hard household rules; never relaxed (the deterministic dislike check)."""
    return not recipe_facts.violations(dish, prefs)


def _family_representatives(dishes: list[Dish]) -> list[Dish]:
    """REC-9: one dish per family, its pinned or best-rated variant (ties: lowest ref)."""
    out: list[Dish] = []
    families: dict[str, list[Dish]] = {}
    for d in dishes:
        if d.family:
            families.setdefault(d.family, []).append(d)
        else:
            out.append(d)
    for members in families.values():
        pinned = [d for d in members if d.family_preferred]
        out.append(pinned[0] if pinned else min(members, key=lambda d: (-(d.favorite or 0), d.ref)))
    return sorted(out, key=lambda d: d.ref)


@dataclass
class _Week:
    refs: set[str] = field(default_factory=set)
    families: set[str] = field(default_factory=set)
    proteins: Counter[str] = field(default_factory=Counter)
    discovered: int = 0

    def add(self, dish: Dish) -> None:
        self.refs.add(dish.ref)
        if dish.family:
            self.families.add(dish.family)
        if ORDER_IN_TAG not in dish.tags:  # a takeout night uses no protein allowance
            self.proteins[recipe_facts.protein(dish)] += 1
        if dish.collection is Collection.DISCOVERED:
            self.discovered += 1


class _Planner:
    def __init__(
        self, inputs: PlanInputs, prefs: HouseholdPrefs, week_start: date, seed: int
    ) -> None:
        self.inputs = inputs
        self.prefs = prefs
        self.week_start = week_start
        self.seed = seed
        self.days = [week_start + timedelta(days=i) for i in range(7)]
        self.dishes = {d.ref: d for d in inputs.dishes}
        self.conflicts: list[str] = []
        # Dinners the night before the last N lunch days should make good leftovers (PLN-3).
        targets = (
            self.lunch_days()[-prefs.max_leftover_lunches :] if prefs.max_leftover_lunches else []
        )
        self.feeder_nights = {d - timedelta(days=1) for d in targets}

    def weeknight(self, day: date) -> bool:
        return Weekday.of(day) in self.prefs.weeknight_days

    def recent(self, dish: Dish) -> bool:
        last = self.inputs.history.get(dish.ref)
        window = timedelta(days=self.prefs.repeat_window_days)
        return last is not None and last >= self.week_start - window

    def jitter(self, ref: str) -> float:
        return random.Random(f"{self.seed}:{ref}").random() * 0.1

    def score(self, dish: Dish, day: date | None) -> float:
        fav = dish.favorite if dish.favorite is not None else NEUTRAL_FAVORITE
        expiring = sum(1 for i in dish.ingredients if i.name in self.inputs.expiring)
        last = self.inputs.history.get(dish.ref)
        novelty = 0.3 if last is None else min((self.week_start - last).days / 60, 1.0) * 0.3
        unknown_time = (
            day is not None and self.weeknight(day) and recipe_facts.active_minutes(dish) is None
        )
        return (
            2.0 * min(fav, 6.0) / 5
            + 0.3 * recipe_facts.season_fit(dish, self.week_start)
            + 1.0 * min(expiring, EXPIRING_CAP)
            + novelty
            - (0.5 if unknown_time else 0.0)
            + self.leftover_fit(dish, day)
            + self.jitter(dish.ref)
        )

    def leftover_fit(self, dish: Dish, day: date | None) -> float:
        if day not in self.feeder_nights:
            return 0.0
        friendly = recipe_facts.leftover_friendly(dish, self.prefs.leftover_exclude_proteins)
        return 0.6 if friendly else -0.6

    def fits(self, dish: Dish, day: date, week: _Week, tier: int) -> bool:
        prefs = self.prefs
        if dish.ref in week.refs or (dish.family and dish.family in week.families):
            return False
        if (
            dish.collection is Collection.DISCOVERED
            and week.discovered >= prefs.max_discovered_dinners
        ):
            return False
        if self.weeknight(day):
            minutes = recipe_facts.active_minutes(dish)
            if minutes is not None and minutes > prefs.max_weeknight_active_minutes:
                return False
        if tier < 1 and self.recent(dish):
            return False
        return tier >= 2 or week.proteins[recipe_facts.protein(dish)] < prefs.max_protein_repeats

    def best(self, dishes: list[Dish], day: date | None) -> Dish | None:
        if not dishes:
            return None
        return max(dishes, key=lambda d: (self.score(d, day), d.ref))

    # --- dinners ---------------------------------------------------------------------------

    def dinners(self) -> dict[date, tuple[Dish, bool]]:
        chosen: dict[date, tuple[Dish, bool]] = {}
        week = _Week()
        for (day, meal), ref in sorted(self.inputs.locked.items()):
            if meal is not Meal.DINNER or day not in self.days:
                continue
            dish = self.dishes.get(ref)
            if dish is None:
                self.conflicts.append(f"{_label(day)}: locked recipe {ref} is not approved")
                continue
            chosen[day] = (dish, True)
            week.add(dish)

        # The base week's standing meals come next; they repeat by design, so the repeat
        # window and weeknight time limit do not apply to them.
        for day in self.days:
            rule = self.prefs.base_week.get(Weekday.of(day))
            if day in chosen or not rule:
                continue
            ref = pick_rotation(rotation(rule), self.inputs.history, self.week_start)
            dish = self.dishes.get(ref)
            if dish is None:
                self.conflicts.append(
                    f"{_label(day)}: base-week meal {ref} is not approved; "
                    "planned from the menu instead"
                )
                continue
            chosen[day] = (dish, False)
            week.add(dish)

        base_refs = {r for rule in self.prefs.base_week.values() for r in rotation(rule)}
        pool = [
            d
            for d in _family_representatives(
                [
                    d
                    for d in self.inputs.dishes
                    if d.role is MealRole.DINNER and d.ref not in base_refs
                ]
            )
            if _allowed(d, self.prefs) and d.family not in week.families
        ]
        # Weeknights have the tightest rules, so they choose first.
        for day in sorted(self.days, key=lambda d: (not self.weeknight(d), d)):
            if day in chosen:
                continue
            for tier, relaxed in ((0, ""), (1, "repeat window"), (2, "protein variety")):
                pick = self.best([d for d in pool if self.fits(d, day, week, tier)], day)
                if pick is not None:
                    if relaxed:
                        self.conflicts.append(f"{_label(day)}: relaxed {relaxed} to fill dinner")
                    break
            if pick is None:
                self.conflicts.append(f"{_label(day)}: no dinner fits the rules")
                continue
            chosen[day] = (pick, False)
            week.add(pick)
            if self.weeknight(day) and recipe_facts.active_minutes(pick) is None:
                self.conflicts.append(
                    f"{_label(day)}: {pick.title} has no hands-on time recorded; "
                    f"check it fits {self.prefs.max_weeknight_active_minutes} minutes"
                )
        return chosen

    # --- lunches ---------------------------------------------------------------------------

    def lunch_days(self) -> list[date]:
        if self.prefs.lunch_servings == 0:
            return []
        return [d for d in self.days if Weekday.of(d) in self.prefs.lunch_days]

    def leftovers(self, dinners: dict[date, tuple[Dish, bool]]) -> dict[date, date]:
        """PLN-3: lunch day -> the dinner it is left over from. Latest days first, so prepped
        components cover the start of the week while they are freshest."""
        fed: dict[date, date] = {}
        for day in reversed(self.lunch_days()):
            if len(fed) >= self.prefs.max_leftover_lunches:
                break
            if (day, Meal.LUNCH) in self.inputs.locked:
                continue
            prev = day - timedelta(days=1)
            if prev in dinners and recipe_facts.leftover_friendly(
                dinners[prev][0], self.prefs.leftover_exclude_proteins
            ):
                fed[day] = prev
        return fed

    def batch(self, role: MealRole) -> Dish | None:
        options = [
            d
            for d in self.inputs.dishes
            if d.role is role and _allowed(d, self.prefs) and not self.recent(d)
        ]
        return self.best(options, None)

    def generic(self, kind: str) -> ComponentSpec | None:
        for spec in sorted(self.inputs.components, key=lambda c: c.name):
            if spec.kind == kind and not any(
                i.name in self.prefs.avoid_ingredients for i in spec.per_serving
            ):
                return spec
        return None

    def templates(self) -> list[tuple[str, tuple[ComponentSpec, ...]]]:
        """Available lunch templates, in rotation order, with their components."""
        recipe_parts: dict[str, ComponentSpec] = {}
        for role, kind in ((MealRole.LUNCH, "main"), (MealRole.SOUP, "soup")):
            if (dish := self.batch(role)) is not None:
                recipe_parts[kind] = from_dish(dish)
        out = []
        for template in TEMPLATES:
            parts = [recipe_parts.get(k) or self.generic(k) for k in template.parts]
            if all(p is not None for p in parts):
                out.append((template.name, tuple(p for p in parts if p is not None)))
        return out


def plan_week(
    inputs: PlanInputs, prefs: HouseholdPrefs, week_start: date, seed: int
) -> WeekPlanResult:
    p = _Planner(inputs, prefs, week_start, seed)
    dinners = p.dinners()
    fed = p.leftovers(dinners)
    feeds = set(fed.values())

    meals: list[PlannedMeal] = []
    for day, (dish, locked) in dinners.items():
        servings = prefs.dinner_servings + (prefs.lunch_servings if day in feeds else 0)
        meals.append(PlannedMeal(day, Meal.DINNER, dish.ref, dish.title, servings, locked=locked))
    for day in p.days:
        if day not in dinners:
            meals.append(PlannedMeal(day, Meal.DINNER, None, "(open)", prefs.dinner_servings))

    uses: dict[str, tuple[ComponentSpec, list[date]]] = {}
    templates = p.templates()
    template_days = 0
    for day in p.lunch_days():
        locked_ref = inputs.locked.get((day, Meal.LUNCH))
        if locked_ref is not None:
            lunch_dish = p.dishes.get(locked_ref)
            if lunch_dish is None:
                p.conflicts.append(f"{_label(day)}: locked lunch {locked_ref} is not approved")
                continue
            parts: tuple[str, ...] = ()
            if lunch_dish.role in (MealRole.SOUP, MealRole.LUNCH):
                spec = from_dish(lunch_dish)
                uses.setdefault(spec.name, (spec, []))[1].append(day)
                parts = (spec.name,)
            meals.append(
                PlannedMeal(
                    day,
                    Meal.LUNCH,
                    lunch_dish.ref,
                    lunch_dish.title,
                    prefs.lunch_servings,
                    None,
                    parts,
                    locked=True,
                )
            )
        elif day in fed:
            dish = dinners[fed[day]][0]
            meals.append(
                PlannedMeal(day, Meal.LUNCH, dish.ref, dish.title, prefs.lunch_servings, fed[day])
            )
        elif templates:
            name, specs = templates[template_days % len(templates)]
            template_days += 1
            for spec in specs:
                uses.setdefault(spec.name, (spec, []))[1].append(day)
            recipe_refs = [s.recipe_ref for s in specs if s.kind == "main" and s.recipe_ref]
            meals.append(
                PlannedMeal(
                    day,
                    Meal.LUNCH,
                    recipe_refs[0] if recipe_refs else None,
                    " + ".join(s.name for s in specs),
                    prefs.lunch_servings,
                    components=tuple(s.name for s in specs),
                    template=name,
                )
            )
        else:
            p.conflicts.append(f"{_label(day)}: no lunch template has its components")

    components = tuple(
        ComponentUse(spec, tuple(days), prefs.lunch_servings * len(days))
        for _, (spec, days) in sorted(uses.items())
    )
    make_ahead = tuple(
        dinners[d][0].ref for d in sorted(dinners) if "make-ahead" in dinners[d][0].tags
    )
    order = {Meal.LUNCH: 0, Meal.DINNER: 1}
    return WeekPlanResult(
        week_start=week_start,
        seed=seed,
        meals=tuple(sorted(meals, key=lambda m: (m.date, order[m.meal]))),
        components=components,
        make_ahead=make_ahead,
        conflicts=tuple(p.conflicts),
    )
