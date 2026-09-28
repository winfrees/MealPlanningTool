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

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Then, in this folder:

```sh
uv run mealctl serve --demo   # try it on sample recipes; log in with: demo
uv run mealctl serve          # your own household
```

The app opens in your browser at http://localhost:8000. The first `mealctl serve` asks you to
choose a household password, then the **Get started** page walks you through importing your
recipe PDF, approving the recipes and planning the week. Press Ctrl+C in the terminal to stop.
More in the [user guide](docs/user-guide.md).

## Development

```sh
uv sync
uv run mealctl --help
make check                     # lint, typecheck, tests (browser tests need Chromium)
```

## Importing the core collection from the terminal

The web app's **Get started** page does this too, and without an API key it can prepare batches
for a Claude chat instead (`mealctl import chat-batches` / `mealctl import chat`), or read the
scans with a local model through Ollama and Docling (`uv sync --extra local`, then
`mealctl import pdf ... --engine local`; see the [user guide](docs/user-guide.md)). From the terminal, put
`Recipes_12Sept26.pdf` in `data/source/` (git-ignored), then:

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
| M4 | Web app: plan, search, review, shop (password-protected) | Code done |
| M5 | Agentic discovery (web scout, URL import) | Code done; import 20 real links |
| M6 | Vision inventory | |
| M7 | Retailer cart + MCP server | |

## Releases

Every merge to `main` that passes CI is published as a GitHub release by
`.github/workflows/release.yml`: the wheel and source archive, with notes generated from the
merged pull requests. Versions follow `pyproject.toml`'s series: `0.1.0` first, then `0.1.1`,
`0.1.2`, ... per merge. Change the version in `pyproject.toml` (for example to `0.2.0`) to start
a new series.
