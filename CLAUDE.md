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
uv run mealctl import chat-batches data/source/Recipes_12Sept26.pdf   # then: import chat REPLY
uv run mealctl local           # check Ollama + Docling; import pdf --engine local uses them
uv run mealctl import url LINK... [--dry-run]   # web recipes via JSON-LD, no agent (ING-2)
uv run mealctl discover run --preset soups [--add]   # scout (key); or: discover prompt / chat
make url-evals                 # M5 acceptance: links in evals/urls.txt import with no agent
uv run mealctl review list     # then review show / approve / merge / family
uv run mealctl plan week --start 2026-10-04 [--seed N]   # then plan show / cards / swap / lock
uv run mealctl prep show       # Sunday prep checklist
uv run mealctl inventory add|list|expiring|staples
uv run mealctl list show [--format md|text|pdf] [--output FILE]
uv run mealctl serve [--demo]  # web app at http://localhost:8000; first run asks for a password
make e2e                       # browser tests (needs: uv run playwright install chromium)
make evals                     # extractor eval; calls the real API
UPDATE_GOLDEN=1 uv run pytest tests/test_golden_week.py  # regenerate golden files, then review
```

## Layout

- `src/mealplan/models/`: `tables.py` (SQLAlchemy 2 storage), `schemas.py` (Pydantic boundary
  types), `manifest.py` (core recipe manifest), `enums.py`.
- `src/mealplan/migrations/`: Alembic. After changing `tables.py`, run
  `uv run mealctl db revision -m "what changed"` against a scratch DB
  (`MEALPLAN_DB_PATH=/tmp/x.db`), review the file, and run `ruff format`.
  `tests/test_db.py::test_migrations_match_models` fails if you forget.
- `src/mealplan/core/`: `units.py`, `parser.py` (ingredient lines), `scaling.py`,
  `normalizer.py` (catalog matching), `library.py` (drafts, copies/variants, families, ratings),
  `preferences.py`, `recipe_facts.py`, `planner.py` (pure; ADR-0005), `components.py`,
  `prep.py`, `render.py` (plan, prep, day cards), `plan_store.py` (DB side of planning),
  `base_week.py` (standing meals, PLN-9; defaults in `preferences.py`), `inventory.py`,
  `shopping.py` (pure list engine; ADR-0006), `render_list.py` (md/text/pdf), `kitchen.py`
  (DB side of lists, cooking and prep deductions), `setup.py` (first run, demo household),
  `discovery.py` (fetch and check web finds: verdicts, add as discovered draft or variant; M5).
- `src/mealplan/ingest/`: `pdf.py` (manifest-driven import), `web_print.py` (deterministic
  path), `chat_import.py` (batches and prompts for a Claude chat, reply parsing; ING-5),
  `url.py` (schema.org Recipe JSON-LD; ING-2), `fetch.py` (public http(s) only, size/time caps),
  `grounding.py`, `review_queue.py`.
- `src/mealplan/agents/extractor.py`: Claude PDF extractor behind the `RecipeExtractor`
  protocol; tests use fakes, never the API.
- `src/mealplan/agents/local_extractor.py`: Docling (OCR/layout, optional `local` extra) plus
  an Ollama model behind the same protocol (ING-7; ADR-0008). `choose.py` picks Claude, local
  or none (`MEALPLAN_EXTRACTOR`) for the CLI and the web app.
- `src/mealplan/agents/scout.py`: web recipe scout (web_search + read-only search_library;
  proposes URLs only) and the no-key chat prompt / reply parser. ADR-0009.
- `src/mealplan/agents/credentials.py`: tidy and check the Anthropic API key (read as
  `MEALPLAN_ANTHROPIC_API_KEY` or `ANTHROPIC_API_KEY`; the web Setup page can save it).
- `src/mealplan/web/`: `app.py` (FastAPI JSON API; thin handlers over the core, same as the
  CLI), `auth.py` (household password, signed cookie, CSRF header), `importer.py` (PDF import
  from the browser), `launch.py` (first-run password, `--demo`), `static/` (no-build
  Preact + htm frontend; `vendor/README.md` lists the vendored files). ADR-0007.
- `src/mealplan/retail/`: adapter protocol only, until M7.
- `data/`: core recipe manifest, ingredient catalog, prep components, `sample_library.json`
  (the demo's recipes and the golden week's library); the source PDF lives in git-ignored
  `data/source/`.
- `tests/golden/week/`: the golden week's expected plan, prep, day cards and list;
  `tests/golden/url/`: saved recipe pages and their expected parse.
- `docs/decisions/`: ADRs; add one per notable choice.

## Releases

`.github/workflows/release.yml` publishes a GitHub release after CI passes on each merge to
`main` (next patch of the `pyproject.toml` series; `scripts/next_version.py`). Bump the
minor or major version in `pyproject.toml` when a milestone warrants it.

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
- **M2 Planner + prep scheduler**: code done; golden week passes. Acceptance needs a real week
  planned from the approved collection and cooked.
- **M3 Inventory + shopping list**: code done; golden week's list checked by hand and pinned.
- **M4 Web app**: code done; API tests and a browser test cover every screen.
- **M5 Agentic discovery**: code done (URL import, scout, chat path, Discover tab, promotion,
  evals). Acceptance needs the network: 20 links in `evals/urls.txt` (`make url-evals`) and
  `evals/run_scout.py` with a key.
- Next: **M6** vision inventory.
