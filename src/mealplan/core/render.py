"""Plain-text views: week plan, prep checklist, and day cards (PLN-6, NFR-9).

Markdown with lists and short lines, so it reads on a phone without horizontal scrolling.
Output is deterministic and pinned by golden files.
"""

from datetime import date, timedelta

from mealplan.core import recipe_facts
from mealplan.core.base_week import ORDER_IN_TAG
from mealplan.core.planner import PlannedMeal, WeekPlanResult
from mealplan.core.prep import PrepPlan
from mealplan.core.recipe_facts import Dish
from mealplan.core.units import format_qty
from mealplan.models.enums import Meal


def _day(d: date) -> str:
    return d.strftime("%a %d %b")


def _minutes(total: int) -> str:
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    return f"{hours} h" if hours else f"{minutes} min"


def _meal_line(m: PlannedMeal) -> str:
    ref = f" ({m.ref})" if m.ref and not m.components else ""
    extra = ""
    if m.leftover_of is not None:
        extra = f", leftovers from {m.leftover_of:%a}"
    elif m.locked:
        extra = ", swapped in"
    return f"- {m.meal.value.title()}: {m.title}{ref} · {format_qty(m.servings)} servings{extra}"


def plan_markdown(result: WeekPlanResult, status: str = "draft") -> str:
    lines = [f"# Week of {_day(result.week_start)} {result.week_start:%Y}", ""]
    lines.append(f"Seed {result.seed} · {status}")
    for day in sorted({m.date for m in result.meals}):
        lines += ["", f"## {_day(day)}"]
        lines += [_meal_line(m) for m in result.meals if m.date == day]
    if result.conflicts:
        lines += ["", "## Check these", *[f"- {c}" for c in result.conflicts]]
    return "\n".join(lines) + "\n"


def prep_markdown(prep: PrepPlan) -> str:
    lines = [f"# Prep: {_day(prep.day)}", ""]
    if not prep.tasks:
        lines.append("Nothing to prep this week.")
        return "\n".join(lines) + "\n"
    lines.append(f"About {_minutes(prep.est_minutes)}. Start the first tasks together.")
    lines.append("")
    for i, s in enumerate(prep.tasks, 1):
        t = s.task
        where = f" on the {', '.join(t.equipment)}" if t.equipment else ""
        timing = f"{t.active} min hands-on"
        if t.passive:
            timing += f", then {t.passive} min{where}"
        note = f" ({t.note})" if t.note else ""
        lines.append(f"{i}. [{_minutes(s.start) if s.start else '0 min'}] {t.name}: {timing}{note}")
    if prep.storage:
        lines += ["", "## Storage", *[f"- {s}" for s in prep.storage]]
    if prep.conflicts:
        lines += ["", "## Check these", *[f"- {c}" for c in prep.conflicts]]
    return "\n".join(lines) + "\n"


def day_card(
    day: date, result: WeekPlanResult, prep: PrepPlan, dishes: dict[str, Dish]
) -> list[str]:
    meals = {(m.date, m.meal): m for m in result.meals}
    tomorrow = day + timedelta(days=1)
    lines = [f"## {_day(day)}"]
    if day == prep.day and prep.tasks:
        lines.append(f"- Prep day: {len(prep.tasks)} tasks, about {_minutes(prep.est_minutes)}")

    dinner = meals.get((day, Meal.DINNER))
    if dinner is not None and dinner.ref:
        lines += _dinner_lines(dinner, meals.get((tomorrow, Meal.LUNCH)), result, prep, dishes)
    elif dinner is not None:
        lines.append("- Dinner: open")

    for item in prep.day_of.get(day, ()):
        lines.append(f"- Also tonight: make {item}")
    for item in prep.thaw.get(day, ()):
        lines.append(f"- Move to the fridge tonight: {item}")

    lunch = meals.get((tomorrow, Meal.LUNCH))
    if lunch is not None:
        what = f"leftover {lunch.title}" if lunch.leftover_of is not None else lunch.title
        lines.append(f"- Pack for {tomorrow:%a} lunch ({format_qty(lunch.servings)}): {what}")
    return lines


def _dinner_lines(
    dinner: PlannedMeal,
    tomorrows_lunch: PlannedMeal | None,
    result: WeekPlanResult,
    prep: PrepPlan,
    dishes: dict[str, Dish],
) -> list[str]:
    lines = [f"- Dinner: {dinner.title}"]
    dish = dishes.get(dinner.ref or "")
    if dish is not None and ORDER_IN_TAG in dish.tags:
        return [*lines, "  - Order in: nothing to cook"]

    made_ahead = dinner.ref in result.make_ahead and dinner.date != prep.day
    feeds = (
        tomorrows_lunch if tomorrows_lunch and tomorrows_lunch.leftover_of == dinner.date else None
    )
    if made_ahead:
        lines.append("  - Made on prep day: reheat")
    elif feeds is not None:
        tonight = format_qty(dinner.servings - feeds.servings)
        lines.append(
            f"  - Cook {format_qty(dinner.servings)} servings: {tonight} tonight, "
            f"{format_qty(feeds.servings)} for lunch"
        )
    else:
        lines.append(f"  - Cook {format_qty(dinner.servings)} servings")

    if not made_ahead:
        minutes = recipe_facts.active_minutes(dish) if dish else None
        lines.append(
            f"  - {minutes} min hands-on"
            if minutes is not None
            else "  - Hands-on time not recorded"
        )
    if dish is not None and "slow cooker" in recipe_facts.equipment(dish):
        hours = recipe_facts.passive_minutes(dish) / 60
        if hours >= 2:
            lines.append(f"  - Start the slow cooker about {format_qty(hours)} h before dinner")
    if feeds is not None:
        lines.append(f"  - Set aside {format_qty(feeds.servings)} portions for tomorrow")
    return lines


def day_cards_markdown(result: WeekPlanResult, prep: PrepPlan, dishes: dict[str, Dish]) -> str:
    days = sorted({m.date for m in result.meals})
    lines = [f"# Day cards: week of {_day(result.week_start)}"]
    for day in days:
        lines += ["", *day_card(day, result, prep, dishes)]
    return "\n".join(lines) + "\n"
