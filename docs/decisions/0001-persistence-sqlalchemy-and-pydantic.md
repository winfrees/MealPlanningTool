# ADR-0001: Persistence with SQLAlchemy 2 tables and Pydantic boundary schemas

- Status: Accepted
- Date: 2026-09-26
- Requirements: NFR-1, NFR-4, ING-3, ING-4, REC-3

## Context

The stack suggests "Pydantic v2, SQLModel or SQLAlchemy 2". Agents, importers, and the CLI all
produce data that must be validated before it reaches the database ("agents propose, the core
disposes"), and the schema has to evolve under Alembic in a single SQLite file.

## Decision

- Storage tables are plain SQLAlchemy 2 declarative models (`src/mealplan/models/tables.py`).
- Boundary types are separate Pydantic v2 models (`src/mealplan/models/schemas.py`), with
  `extra="forbid"`, so a malformed agent proposal fails loudly.
- Alembic migrations live inside the package (`src/mealplan/migrations/`) so `mealctl db upgrade`
  works from an installed wheel. A test fails if the models drift from the migrations.
- Enums are stored as strings (`native_enum=False`) so SQLite stays readable and migrations stay
  simple.
- Provenance is a child table, `recipe_source`, with one row per saved copy (file + pages, or URL),
  so collapsing copies (REC-3) keeps every page reference.

## Consequences

- Two model layers mean a little mapping code, but a draft recipe can exist and be reviewed without
  touching the database, which is the review-queue workflow (ING-3).
- SQLModel's single-class convenience is given up in exchange for mature SQLAlchemy 2 typing and
  Alembic autogenerate.
