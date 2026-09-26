.PHONY: install lint typecheck test check evals

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest --cov

check: lint typecheck test

# Per-agent eval sets (see evals/README.md). Calls the real API.
evals:
	uv run python evals/run_extractor.py
