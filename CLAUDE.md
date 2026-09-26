# CLAUDE.md

Household meal planner. **Read `docs/requirements.md` first**: it is the spec, and its requirement
IDs (REC-3, PLN-5, NFR-1, …) go in commit messages, test names/docstrings, and ADRs.

## Principles (requirements §1)

1. **Deterministic core, agentic edges.** Planning, scaling, units, inventory, and lists are plain
   tested Python. Same inputs + seed → same output.
2. **Agents propose, the core disposes.** Agents never write to the DB. They return Pydantic
   proposals; a person approves writes.
3. **Prep-first.** One Sunday prep session feeds the week's dinners and lunches.
4. **Leftovers are a feature.** Dinners are sized so they become next-day lunches.
5. **Provenance everywhere.** Every recipe and inventory item records its source and confidence.
6. **Local-first.** One SQLite file, plain-text exports.

## Commands

```sh
uv sync                    # install (Python 3.12+)
make check                 # ruff + ruff format --check + mypy (strict) + pytest with coverage
uv run pytest tests/test_manifest.py -q
uv run mealctl --help
uv run mealctl db upgrade  # create/migrate the SQLite DB (MEALPLAN_DB_PATH, default mealplan.db)
uv run mealctl manifest check
uv run mealctl catalog seed    # load data/ingredients.csv
uv run mealctl import pdf data/source/Recipes_12Sept26.pdf [--no-agent] [--only core-001]
uv run mealctl review list     # then review show / approve / merge / family
make evals                     # extractor eval; calls the real API
```

## Layout

- `src/mealplan/models/`: `tables.py` (SQLAlchemy 2 storage), `schemas.py` (Pydantic boundary
  types), `manifest.py` (core recipe manifest), `enums.py`.
- `src/mealplan/migrations/`: Alembic. After changing `tables.py`, run
  `uv run mealctl db revision -m "what changed"` against a scratch DB
  (`MEALPLAN_DB_PATH=/tmp/x.db`), review the file, and run `ruff format`.
  `tests/test_db.py::test_migrations_match_models` fails if you forget.
- `src/mealplan/core/`: `units.py`, `parser.py` (ingredient lines), `scaling.py`,
  `normalizer.py` (catalog matching), `library.py` (drafts, copies/variants, families, ratings).
- `src/mealplan/ingest/`: `pdf.py` (manifest-driven import), `web_print.py` (deterministic
  path), `grounding.py`, `review_queue.py`.
- `src/mealplan/agents/extractor.py`: Claude PDF extractor behind the `RecipeExtractor`
  protocol; tests use fakes, never the API.
- `src/mealplan/retail/`: adapter protocol only, until M6.
- `data/`: core recipe manifest; the source PDF lives in git-ignored `data/source/`.
- `docs/decisions/`: ADRs; add one per notable choice.

## Working rules

- One milestone per branch. Name the milestone and requirement IDs in scope at the start.
- Write golden tests (`tests/golden/`) before implementing a milestone.
- Keep agents behind interfaces in `agents/` so core tests use fakes and never call the API.
- Never commit secrets (`.env` is ignored; gitleaks runs in pre-commit) or household data
  (`*.db`, `data/source/`, photos).

## Status

- **M0 Foundations**: done except the retailer access check (ADR-0002).
- **M1 Core recipe library + normalizer**: code done. Acceptance needs the real PDF: run the
  import, approve the collection, and check the unmatched-line rate (target: 95%+ clean).
- Next: **M2** planner + prep scheduler (use plan mode first).
