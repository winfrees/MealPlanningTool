.PHONY: install lint typecheck test e2e check evals serve

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest -m "not e2e" --cov

# Browser tests of the web app (needs Chromium: `uv run playwright install chromium`).
e2e:
	uv run pytest -m e2e

serve:
	uv run mealctl serve

check: lint typecheck test e2e

# Per-agent eval sets (see evals/README.md). Calls the real API.
evals:
	uv run python evals/run_extractor.py
