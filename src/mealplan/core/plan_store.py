"""Database side of planning: load `PlanInputs`, save and reload a week (PLN-7).

Saved meal slots are the record of truth for a week; `saved_plan` rebuilds the result from
them, so later rating changes never silently rewrite a planned week.
"""

from dataclasses import replace
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mealplan.core import library
from mealplan.core.components import ComponentSpec, from_dish, generic_from_db
from mealplan.core.planner import (
    ComponentUse,
    PlanInputs,
    PlannedMeal,
    WeekPlanResult,
    plan_week,
)
from mealplan.core.preferences import HouseholdPrefs, Weekday
from mealplan.core.prep import PrepPlan, build_prep
from mealplan.core.recipe_facts import Dish, DishIngredient, StepTime
from mealplan.models.enums import Meal, RecipeStatus
from mealplan.models.tables import (
    Component,
    Ingredient,
    InventoryItem,
    MealSlot,
    PrepSession,
    Recipe,
    WeekPlan,
)

HISTORY_DAYS = 90
EXPIRING_DAYS = 4


class PlanError(ValueError):
    pass


def dish_from_recipe(session: Session, recipe: Recipe) -> Dish:
    favorite = library.favorite_score(session, recipe)
    return Dish(
        ref=recipe.ref,
        title=recipe.title,
        role=recipe.meal_role,
        servings=recipe.servings,
        collection=recipe.collection,
        family=recipe.family.name if recipe.family else None,
        family_preferred=bool(recipe.family and recipe.family.preferred_recipe_id == recipe.id),
        tags=frozenset(recipe.tags),
        ingredients=tuple(
            DishIngredient(
                i.ingredient.canonical_name if i.ingredient else i.raw_text, i.qty, i.unit
            )
            for i in recipe.ingredients
        ),
        steps=tuple(
            StepTime(s.text, s.active_minutes, s.passive_minutes, tuple(s.equipment))
            for s in recipe.steps
        ),
        prep_minutes=recipe.prep_minutes,
        cook_minutes=recipe.cook_minutes,
        favorite=favorite or None,
    )


def _week_slots(session: Session, week_start: date) -> list[MealSlot]:
    return list(
        session.scalars(
            select(MealSlot)
            .where(MealSlot.date >= week_start, MealSlot.date < week_start + timedelta(days=7))
            .order_by(MealSlot.date, MealSlot.meal)
        )
    )


def _refs_by_id(session: Session) -> dict[int, str]:
    return {rid: ref for rid, ref in session.execute(select(Recipe.id, Recipe.ref))}


def load_inputs(session: Session, week_start: date, keep_dinners: bool = False) -> PlanInputs:
    """Planner inputs from the database.

    Manual overrides in the week are always locked. `keep_dinners` also locks every current
    dinner, so a swap moves only the swapped meal and what depends on it.
    """
    approved = session.scalars(select(Recipe).where(Recipe.status == RecipeStatus.APPROVED))
    dishes = tuple(dish_from_recipe(session, r) for r in sorted(approved, key=lambda r: r.ref))

    history: dict[str, date] = {}
    rows = session.execute(
        select(Recipe.ref, MealSlot.date)
        .join(MealSlot, MealSlot.recipe_id == Recipe.id)
        .where(
            MealSlot.date < week_start,
            MealSlot.date >= week_start - timedelta(days=HISTORY_DAYS),
        )
    )
    for ref, day in rows:
        history[ref] = max(day, history.get(ref, day))

    expiring = frozenset(
        session.scalars(
            select(Ingredient.canonical_name)
            .join(InventoryItem, InventoryItem.ingredient_id == Ingredient.id)
            .where(
                InventoryItem.best_by.is_not(None),
                InventoryItem.best_by <= week_start + timedelta(days=EXPIRING_DAYS),
            )
        )
    )

    refs = _refs_by_id(session)
    locked: dict[tuple[date, Meal], str] = {}
    for slot in _week_slots(session, week_start):
        if slot.recipe_id is None:
            continue
        if slot.is_override or (keep_dinners and slot.meal is Meal.DINNER):
            locked[(slot.date, slot.meal)] = refs[slot.recipe_id]

    return PlanInputs(
        dishes=dishes,
        components=tuple(generic_from_db(session)),
        history=history,
        expiring=expiring,
        locked=locked,
    )


def _component_row(session: Session, spec: ComponentSpec, recipe_ids: dict[str, int]) -> int:
    row = session.scalars(select(Component).where(Component.name == spec.name)).one_or_none()
    if row is None:
        row = Component(name=spec.name)
        session.add(row)
    if spec.recipe_ref is not None:  # recipe components follow their recipe
        row.recipe_id = recipe_ids.get(spec.recipe_ref)
        row.kind = spec.kind
        row.keeps_days = spec.keeps_days
        row.freezer_ok = spec.freezer_ok
        row.active_minutes = spec.active_minutes
        row.passive_minutes = spec.passive_minutes
        row.equipment = list(spec.equipment)
    session.flush()
    return row.id


def get_week(session: Session, week_start: date) -> WeekPlan | None:
    return session.scalars(select(WeekPlan).where(WeekPlan.week_start == week_start)).one_or_none()


def save_plan(
    session: Session,
    result: WeekPlanResult,
    overrides: set[tuple[date, Meal]] | None = None,
    force: bool = False,
) -> WeekPlan:
    """Replace the week's meal slots with `result`. A locked week needs `force`."""
    week = get_week(session, result.week_start)
    if week is not None and week.status == "locked" and not force:
        raise PlanError(f"week of {result.week_start} is locked; unlock it to re-plan")
    if week is None:
        week = WeekPlan(week_start=result.week_start, seed=result.seed)
        session.add(week)
    week.seed = result.seed
    week.conflicts = list(result.conflicts)

    overrides = (
        overrides if overrides is not None else {(m.date, m.meal) for m in result.meals if m.locked}
    )
    in_week = (
        MealSlot.date >= result.week_start,
        MealSlot.date < result.week_start + timedelta(days=7),
    )
    # Leftover lunches reference their dinners, so they go first.
    session.execute(delete(MealSlot).where(*in_week, MealSlot.is_leftover_of.is_not(None)))
    session.execute(delete(MealSlot).where(*in_week))
    recipe_ids = {ref: rid for rid, ref in _refs_by_id(session).items()}
    specs = {use.spec.name: use.spec for use in result.components}
    dinner_ids: dict[date, int] = {}
    for meal in sorted(result.meals, key=lambda m: (m.meal is Meal.LUNCH, m.date)):
        slot = MealSlot(
            date=meal.date,
            meal=meal.meal,
            recipe_id=recipe_ids.get(meal.ref) if meal.ref else None,
            component_ids=[
                _component_row(session, specs[n], recipe_ids) for n in meal.components if n in specs
            ],
            servings=meal.servings,
            is_leftover_of=dinner_ids.get(meal.leftover_of) if meal.leftover_of else None,
            is_override=(meal.date, meal.meal) in overrides,
        )
        session.add(slot)
        session.flush()
        if meal.meal is Meal.DINNER:
            dinner_ids[meal.date] = slot.id
    session.flush()
    return week


def saved_plan(session: Session, week_start: date) -> WeekPlanResult:
    """Rebuild a saved week from its meal slots."""
    week = get_week(session, week_start)
    if week is None:
        raise PlanError(f"no plan for the week of {week_start}; run `mealctl plan week`")
    slots = _week_slots(session, week_start)
    by_id = {s.id: s for s in slots}
    recipes = {r.id: r for r in session.scalars(select(Recipe))}
    components = {c.id: c for c in session.scalars(select(Component))}
    generic = {c.name: c for c in generic_from_db(session)}

    meals = []
    uses: dict[str, tuple[ComponentSpec, list[date]]] = {}
    for s in slots:
        recipe = recipes.get(s.recipe_id) if s.recipe_id else None
        names = tuple(components[c].name for c in s.component_ids if c in components)
        for cid in s.component_ids:
            comp = components.get(cid)
            if comp is None:
                continue
            spec = generic.get(comp.name)
            if spec is None and comp.recipe_id in recipes:
                spec = from_dish(dish_from_recipe(session, recipes[comp.recipe_id]))
            if spec is not None:
                uses.setdefault(spec.name, (spec, []))[1].append(s.date)
        leftover = by_id[s.is_leftover_of].date if s.is_leftover_of in by_id else None
        title = recipe.title if recipe else (" + ".join(names) or "(open)")
        meals.append(
            PlannedMeal(
                date=s.date,
                meal=s.meal,
                ref=recipe.ref if recipe else None,
                title=title,
                servings=s.servings,
                leftover_of=leftover,
                components=names,
                locked=s.is_override,
            )
        )
    lunch_servings = {m.date: m.servings for m in meals if m.meal is Meal.LUNCH}
    tags = {r.ref: r.tags for r in recipes.values()}
    make_ahead = tuple(
        m.ref
        for m in sorted(meals, key=lambda m: m.date)
        if m.meal is Meal.DINNER and m.ref and "make-ahead" in tags[m.ref]
    )
    order = {Meal.LUNCH: 0, Meal.DINNER: 1}
    return WeekPlanResult(
        week_start=week_start,
        seed=week.seed,
        meals=tuple(sorted(meals, key=lambda m: (m.date, order[m.meal]))),
        components=tuple(
            ComponentUse(spec, tuple(days), sum(lunch_servings.get(d, 0) for d in days))
            for _, (spec, days) in sorted(uses.items())
        ),
        make_ahead=make_ahead,
        conflicts=tuple(week.conflicts),
    )


def default_seed(week_start: date) -> int:
    return int(week_start.strftime("%Y%m%d"))


def plan_and_save(
    session: Session,
    week_start: date,
    prefs: HouseholdPrefs,
    seed: int | None = None,
    force: bool = False,
) -> WeekPlanResult:
    """PLN-1/7: plan a week (keeping manual overrides) and save it. Re-planning reuses the
    week's stored seed unless a new one is given."""
    existing = get_week(session, week_start)
    if seed is None:
        seed = existing.seed if existing is not None else default_seed(week_start)
    inputs = load_inputs(session, week_start)
    result = plan_week(inputs, prefs, week_start, seed)
    save_plan(session, result, force=force)
    save_prep(session, build_prep(result, {d.ref: d for d in inputs.dishes}))
    return result


def swap(
    session: Session, week_start: date, day: date, meal: Meal, ref: str, prefs: HouseholdPrefs
) -> WeekPlanResult:
    """PLN-7: a manual swap, recorded as an override. Other dinners stay put; leftovers,
    lunches and prep are recomputed around the change."""
    week = get_week(session, week_start)
    if week is None:
        raise PlanError(f"no plan for the week of {week_start}")
    if week.status == "locked":
        raise PlanError(f"week of {week_start} is locked; unlock it to swap")
    if not week_start <= day < week_start + timedelta(days=7):
        raise PlanError(f"{day} is not in the week of {week_start}")
    recipe = library.get_recipe(session, ref)
    if recipe.status is not RecipeStatus.APPROVED:
        raise PlanError(f"{ref} is not approved yet")

    inputs = load_inputs(session, week_start, keep_dinners=True)
    overrides = {(s.date, s.meal) for s in _week_slots(session, week_start) if s.is_override}
    overrides.add((day, meal))
    locked = {**inputs.locked, (day, meal): ref}
    result = plan_week(replace(inputs, locked=locked), prefs, week_start, week.seed)
    # Dinners were only pinned to hold them in place; "locked" means a manual override.
    result = replace(
        result,
        meals=tuple(replace(m, locked=(m.date, m.meal) in overrides) for m in result.meals),
    )
    save_plan(session, result, overrides=overrides)
    save_prep(session, build_prep(result, {d.ref: d for d in inputs.dishes}))
    return result


def set_locked(session: Session, week_start: date, locked: bool) -> WeekPlan:
    week = get_week(session, week_start)
    if week is None:
        raise PlanError(f"no plan for the week of {week_start}")
    week.status = "locked" if locked else "draft"
    session.flush()
    return week


def mark_cooked(session: Session, day: date, meal: Meal = Meal.DINNER) -> MealSlot:
    slot = session.scalars(
        select(MealSlot).where(MealSlot.date == day, MealSlot.meal == meal)
    ).one_or_none()
    if slot is None or slot.recipe_id is None:
        raise PlanError(f"nothing planned for {meal} on {day}")
    slot.cooked = True
    session.flush()
    return slot


def next_prep_day(today: date, prefs: HouseholdPrefs) -> date:
    """The prep day on or after `today`: the default start of the week to plan."""
    target = list(Weekday).index(prefs.prep_day)
    return today + timedelta(days=(target - today.weekday()) % 7)


def plan_dishes(session: Session, result: WeekPlanResult) -> dict[str, Dish]:
    """Dish snapshots for every recipe a saved week refers to."""
    refs = {m.ref for m in result.meals if m.ref}
    refs |= {u.spec.recipe_ref for u in result.components if u.spec.recipe_ref}
    recipes = session.scalars(select(Recipe).where(Recipe.ref.in_(refs)))
    return {r.ref: dish_from_recipe(session, r) for r in recipes}


def save_prep(session: Session, prep: PrepPlan) -> PrepSession:
    """PLN-5: the generated prep session, stored for the week (never hand-entered)."""
    row = session.scalars(select(PrepSession).where(PrepSession.date == prep.day)).one_or_none()
    if row is None:
        row = PrepSession(date=prep.day)
        session.add(row)
    names = {s.task.component for s in prep.tasks if s.task.component}
    row.component_ids = sorted(
        session.scalars(select(Component.id).where(Component.name.in_(names)))
    )
    row.tasks = [
        {
            "start": s.start,
            "name": s.task.name,
            "active": s.task.active,
            "passive": s.task.passive,
            "equipment": list(s.task.equipment),
            "note": s.task.note,
        }
        for s in prep.tasks
    ]
    row.est_minutes = prep.est_minutes
    session.flush()
    return row
