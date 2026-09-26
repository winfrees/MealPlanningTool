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

# Per-agent eval sets (see evals/README.md). No agents exist yet (M0).
evals:
	@echo "No eval sets yet; the PDF extractor's set arrives in M1."
