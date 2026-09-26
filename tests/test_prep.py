"""PLN-5: prep session storage rules and critical-path scheduling."""

from collections.abc import Sequence
from datetime import date, timedelta

from mealplan.core.components import ComponentSpec
from mealplan.core.planner import ComponentUse, PlannedMeal, WeekPlanResult
from mealplan.core.prep import PrepTask, build_prep, schedule
from mealplan.core.recipe_facts import Dish, StepTime
from mealplan.models.enums import Meal, MealRole

SUN = date(2026, 10, 4)


def day(n: int) -> date:
    return SUN + timedelta(days=n)


def result(
    components: Sequence[ComponentUse] = (),
    make_ahead: Sequence[str] = (),
    meals: Sequence[PlannedMeal] = (),
) -> WeekPlanResult:
    return WeekPlanResult(SUN, 1, tuple(meals), tuple(components), tuple(make_ahead), ())


def spec(
    name: str,
    keeps: int = 4,
    freezer: bool = False,
    active: int = 10,
    passive: int = 20,
    eq: tuple[str, ...] = ("stove",),
    ref: str | None = None,
) -> ComponentSpec:
    return ComponentSpec(
        name, "soup" if ref else "grain", keeps, freezer, active, passive, eq, (), ref
    )


def test_schedule_starts_long_passive_tasks_first():
    tasks = [
        PrepTask("chop veg", 15, 0),
        PrepTask("soup", 10, 40, ("stove",)),
        PrepTask("roast chicken", 10, 35, ("oven",)),
    ]
    placed, makespan = schedule(tasks)
    starts = {s.task.name: s.start for s in placed}
    assert starts == {"soup": 0, "roast chicken": 10, "chop veg": 20}
    assert makespan == 55  # roast: 10 + 10 + 35


def test_schedule_respects_a_single_oven():
    tasks = [PrepTask("a", 5, 30, ("oven",)), PrepTask("b", 5, 20, ("oven",))]
    placed, makespan = schedule(tasks)
    starts = {s.task.name: s.start for s in placed}
    assert starts == {"a": 0, "b": 35}
    assert makespan == 60


def test_schedule_is_empty_for_no_tasks():
    assert schedule([]) == ((), 0)


def test_within_keeps_days_goes_in_the_fridge():
    use = ComponentUse(spec("quinoa", keeps=5), (day(1), day(2)), 4)
    prep = build_prep(result([use]), {})
    assert [s.task.name for s in prep.tasks] == ["quinoa (4 servings)"]
    assert prep.storage == () and prep.thaw == {} and prep.conflicts == ()


def test_late_use_is_frozen_and_thawed_the_night_before():
    use = ComponentUse(spec("soup", keeps=4, freezer=True), (day(1), day(5)), 4)
    prep = build_prep(result([use]), {})
    assert prep.storage == ("Freeze 2 servings of soup for Fri; thaw Thu night.",)
    assert prep.thaw == {day(4): ("soup (2 servings) for Fri lunch",)}


def test_short_task_that_will_not_keep_is_made_fresh():
    use = ComponentUse(spec("greens", keeps=2, active=10, passive=0, eq=()), (day(4),), 2)
    prep = build_prep(result([use]), {})
    assert prep.tasks == ()  # nothing to do on Sunday
    assert prep.day_of == {day(3): ("greens for Thu lunch (10 min)",)}


def test_long_task_that_will_not_keep_is_a_conflict():
    use = ComponentUse(spec("salad", keeps=2, active=25), (day(5),), 2)
    prep = build_prep(result([use]), {})
    assert prep.conflicts == ("salad keeps 2 days, not until Fri",)


def test_recipe_component_cooks_the_whole_batch_and_freezes_the_rest():
    soup = Dish("core-038", "Squash Soup", MealRole.SOUP, servings=6)
    use = ComponentUse(spec("Squash Soup", freezer=True, ref="core-038"), (day(2),), 2)
    prep = build_prep(result([use]), {"core-038": soup})
    task = prep.tasks[0].task
    assert task.name == "Squash Soup (6 servings)"
    assert task.note == "freeze 4 extra servings"


def test_make_ahead_dinner_is_cooked_on_prep_day():
    enchiladas = Dish(
        "core-051",
        "Enchiladas",
        MealRole.DINNER,
        servings=8,
        tags=frozenset({"make-ahead", "freezer-friendly"}),
        steps=(StepTime("Cook.", 25, 10, ("stove",)), StepTime("Bake.", 20, 30, ("oven",))),
    )
    meal = PlannedMeal(day(5), Meal.DINNER, "core-051", "Enchiladas", 4)
    prep = build_prep(result(make_ahead=["core-051"], meals=[meal]), {"core-051": enchiladas})
    assert prep.tasks[0].task.name == "Enchiladas (4 servings, for Fri)"
    assert prep.tasks[0].task.equipment == ("oven", "stove")
    assert prep.thaw == {day(4): ("Enchiladas for Fri dinner",)}


def test_non_freezable_recipe_component_is_scaled_to_what_is_eaten():
    salad = Dish("core-070", "Quinoa Salad", MealRole.LUNCH, servings=6)
    use = ComponentUse(spec("Quinoa Salad", freezer=False, ref="core-070"), (day(1),), 2)
    prep = build_prep(result([use]), {"core-070": salad})
    assert prep.tasks[0].task.name == "Quinoa Salad (2 servings)"
    assert prep.tasks[0].task.note == ""
