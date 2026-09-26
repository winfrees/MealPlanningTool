"""Sunday prep session (PLN-5): one session, keeps_days respected, critical-path order.

Scheduling is list scheduling with one cook and limited equipment: the cook is busy only for
a task's hands-on minutes, the equipment for hands-on plus passive minutes. Long passive and
oven/simmer tasks start first; hands-on work fills the gaps.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from mealplan.core import recipe_facts
from mealplan.core.components import ComponentSpec
from mealplan.core.planner import WeekPlanResult
from mealplan.core.recipe_facts import Dish
from mealplan.core.units import format_qty
from mealplan.models.enums import Meal

EQUIPMENT_SLOTS = {"oven": 1, "stove": 4, "slow cooker": 1, "pressure cooker": 1, "grill": 1}
DAY_OF_MAX_MINUTES = 10
MAKE_AHEAD_KEEPS = 4


@dataclass(frozen=True)
class PrepTask:
    name: str
    active: int
    passive: int
    equipment: tuple[str, ...] = ()
    note: str = ""
    component: str | None = None  # the component this task makes, if any


@dataclass(frozen=True)
class ScheduledTask:
    start: int  # minutes after the session starts
    task: PrepTask


@dataclass(frozen=True)
class PrepPlan:
    day: date
    tasks: tuple[ScheduledTask, ...]
    est_minutes: int
    storage: tuple[str, ...]
    thaw: dict[date, tuple[str, ...]] = field(default_factory=dict)  # move to fridge that night
    day_of: dict[date, tuple[str, ...]] = field(default_factory=dict)  # make fresh the night before
    conflicts: tuple[str, ...] = ()


def schedule(tasks: list[PrepTask]) -> tuple[tuple[ScheduledTask, ...], int]:
    """Order tasks on one cook and limited equipment. Returns (tasks by start, makespan)."""
    order = sorted(tasks, key=lambda t: (-t.passive, -(t.active + t.passive), t.name))
    cook_free = 0
    slots = {eq: [0] * n for eq, n in EQUIPMENT_SLOTS.items()}
    placed: list[ScheduledTask] = []
    makespan = 0
    for t in order:
        start = cook_free
        for eq in t.equipment:
            if eq in slots:
                start = max(start, min(slots[eq]))
        cook_free = start + t.active
        end = start + t.active + t.passive
        for eq in t.equipment:
            if eq in slots:
                i = slots[eq].index(min(slots[eq]))
                slots[eq][i] = end
        makespan = max(makespan, end)
        placed.append(ScheduledTask(start, t))
    return tuple(sorted(placed, key=lambda s: (s.start, s.task.name))), makespan


def _label(day: date) -> str:
    return day.strftime("%a")


def build_prep(result: WeekPlanResult, dishes: dict[str, Dish]) -> PrepPlan:
    prep_day = result.week_start
    tasks: list[PrepTask] = []
    storage: list[str] = []
    thaw: dict[date, list[str]] = {}
    day_of: dict[date, list[str]] = {}
    conflicts: list[str] = []

    def plan_storage(spec: ComponentSpec, days: tuple[date, ...], per_day: float) -> bool:
        """Storage for each use day. True when the prep-day batch is needed at all."""
        needed = False
        for day in days:
            offset = (day - prep_day).days
            portions = format_qty(per_day)
            if offset <= spec.keeps_days:
                needed = True
            elif spec.freezer_ok:
                needed = True
                storage.append(
                    f"Freeze {portions} servings of {spec.name} for {_label(day)}; "
                    f"thaw {_label(day - timedelta(days=1))} night."
                )
                thaw.setdefault(day - timedelta(days=1), []).append(
                    f"{spec.name} ({portions} servings) for {_label(day)} lunch"
                )
            elif spec.active_minutes <= DAY_OF_MAX_MINUTES:
                day_of.setdefault(day - timedelta(days=1), []).append(
                    f"{spec.name} for {_label(day)} lunch ({spec.active_minutes} min)"
                )
            else:
                conflicts.append(
                    f"{spec.name} keeps {spec.keeps_days} days, not until {_label(day)}"
                )
        return needed

    for use in result.components:
        spec = use.spec
        per_day = use.servings / len(use.days) if use.days else 0
        if not plan_storage(spec, use.days, per_day):
            continue
        dish = dishes.get(spec.recipe_ref) if spec.recipe_ref else None
        if dish is not None:
            # Freezable batches cook the whole recipe; others are scaled to what is eaten.
            full = max(dish.servings or use.servings, use.servings)
            batch = full if spec.freezer_ok else use.servings
            extra = batch - use.servings
            note = f"freeze {format_qty(extra)} extra servings" if extra and spec.freezer_ok else ""
            name = f"{spec.name} ({format_qty(batch)} servings)"
        else:
            name, note = f"{spec.name} ({format_qty(use.servings)} servings)", ""
        tasks.append(
            PrepTask(
                name, spec.active_minutes, spec.passive_minutes, spec.equipment, note, spec.name
            )
        )

    dinners = {m.ref: m for m in result.meals if m.meal is Meal.DINNER and m.ref}
    for ref in result.make_ahead:
        dish, meal = dishes[ref], dinners[ref]
        offset = (meal.date - prep_day).days
        if offset > MAKE_AHEAD_KEEPS:
            if "freezer-friendly" not in dish.tags:
                conflicts.append(
                    f"{dish.title} is make-ahead but will not keep until {_label(meal.date)}"
                )
                continue
            storage.append(f"Freeze {dish.title} for {_label(meal.date)}; thaw the night before.")
            thaw.setdefault(meal.date - timedelta(days=1), []).append(
                f"{dish.title} for {_label(meal.date)} dinner"
            )
        tasks.append(
            PrepTask(
                f"{dish.title} ({format_qty(meal.servings)} servings, for {_label(meal.date)})",
                recipe_facts.active_minutes(dish) or 30,
                recipe_facts.passive_minutes(dish),
                recipe_facts.equipment(dish),
            )
        )

    scheduled, makespan = schedule([t for t in tasks if t.active or t.passive])
    return PrepPlan(
        day=prep_day,
        tasks=scheduled,
        est_minutes=makespan,
        storage=tuple(storage),
        thaw={d: tuple(v) for d, v in sorted(thaw.items())},
        day_of={d: tuple(v) for d, v in sorted(day_of.items())},
        conflicts=tuple(conflicts),
    )
