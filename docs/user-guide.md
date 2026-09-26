# The weekly routine

Five commands, Friday to Sunday. Dates default to the coming prep day (Sunday).

```sh
uv run mealctl plan week            # Friday: draft the week (dinners, lunches, conflicts)
uv run mealctl plan swap 2026-10-06 dinner core-048   # change anything you don't want
uv run mealctl plan lock            # lock it once it looks right
uv run mealctl prep show            # Sunday: the prep checklist, in the order to start things
uv run mealctl plan cards           # the week's day cards: tonight's dinner, tomorrow's lunch
```

After a meal: `uv run mealctl rate core-044 5 --repeat` and `uv run mealctl plan cooked 2026-10-07`.

Household rules live in `mealctl prefs show`; change one with, for example,
`mealctl prefs set dinner_servings 4` or `mealctl prefs set avoid_ingredients '["shrimp"]'`.
Tag a recipe `mild` if the spice estimate is wrong, `very-spicy` to exclude it,
`weeknight-friendly` if its time is unknown but quick, and `no-leftovers` if it does not reheat.

Shopping lists arrive with M3.
