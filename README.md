# MealPlanningTool

A household meal planner that turns a weekly plan into one Sunday prep session, daily dinners and
lunches (with leftovers on purpose), and a shopping list netted against what is already in the
kitchen. A deterministic Python core does the planning and math; Claude agents work only at the
messy edges: PDF and web recipe import, fridge photos, and suggestions.

- Spec: [`docs/requirements.md`](docs/requirements.md)
- Decisions: [`docs/decisions/`](docs/decisions/)
- Core recipe collection: [`data/core_recipe_manifest.json`](data/core_recipe_manifest.json)

## Quick start

```sh
uv sync
uv run mealctl --help
uv run mealctl db upgrade      # creates mealplan.db
uv run mealctl manifest check  # summarizes the 93 core recipes
make check                     # lint, typecheck, tests
```

## Roadmap

| # | Milestone | Status |
| --- | --- | --- |
| M0 | Foundations: repo, CI, SQLite + Alembic, models, CLI | Done, except the retailer access check |
| M1 | Core recipe library + ingredient normalizer | Next |
| M2 | Planner + prep scheduler | |
| M3 | Inventory + shopping list | |
| M4 | Agentic discovery (web scout, URL import) | |
| M5 | Vision inventory | |
| M6 | Retailer cart + MCP server + phone view | |
