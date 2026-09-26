# MealPlanningTool

A household meal planner that turns a weekly plan into one Sunday prep session, daily dinners and
lunches (with leftovers on purpose), and a shopping list netted against what is already in the
kitchen. A deterministic Python core does the planning and math; Claude agents work only at the
messy edges: PDF and web recipe import, fridge photos, and suggestions.

- Spec: [`docs/requirements.md`](docs/requirements.md)
- Decisions: [`docs/decisions/`](docs/decisions/)
- Core recipe collection: [`data/core_recipe_manifest.json`](data/core_recipe_manifest.json)
- Weekly routine: [`docs/user-guide.md`](docs/user-guide.md)

## Quick start

```sh
uv sync
uv run mealctl --help
uv run mealctl db upgrade      # creates mealplan.db
uv run mealctl manifest check  # summarizes the 93 core recipes
uv run mealctl catalog seed    # loads the ingredient catalog
make check                     # lint, typecheck, tests
```

## Importing the core collection

Put `Recipes_12Sept26.pdf` in `data/source/` (git-ignored), then:

```sh
uv run mealctl import pdf data/source/Recipes_12Sept26.pdf --no-agent  # web prints only, free
uv run mealctl import pdf data/source/Recipes_12Sept26.pdf             # the rest, via Claude
uv run mealctl review list                                             # drafts and their issues
uv run mealctl review show core-024
uv run mealctl review approve core-024
uv run mealctl recipes show core-024 --servings 2
```

## Roadmap

| # | Milestone | Status |
| --- | --- | --- |
| M0 | Foundations: repo, CI, SQLite + Alembic, models, CLI | Done, except the retailer access check |
| M1 | Core recipe library + ingredient normalizer | Code done; import and approve the real collection |
| M2 | Planner + prep scheduler | Code done; plan and cook a real week |
| M3 | Inventory + shopping list | Code done |
| M4 | Web app: plan, search, review, shop (password-protected) | In progress |
| M5 | Agentic discovery (web scout, URL import) | |
| M6 | Vision inventory | |
| M7 | Retailer cart + MCP server | |
