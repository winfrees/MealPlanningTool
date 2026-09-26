from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from mealplan import db
from mealplan.models.enums import Collection, RecipeStatus, SourceKind
from mealplan.models.tables import Base, Recipe, RecipeFamily, RecipeSource, Step


def test_migrations_create_every_table(db_url):
    db.upgrade(db_url)
    engine = db.make_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables


def test_migrations_match_models(db_url):
    """Fails when a model changes without a new Alembic revision."""
    db.upgrade(db_url)
    engine = db.make_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_recipe_round_trip_with_family_and_sources(db_url):
    db.upgrade(db_url)
    engine = db.make_engine(db_url)
    with db.session_scope(engine) as s:
        family = RecipeFamily(name="injera")
        recipe = Recipe(ref="core-002", title="Authentic Injera", family=family)
        recipe.sources = [
            RecipeSource(kind=SourceKind.PDF, file="Recipes_12Sept26.pdf", pages="4-6"),
            RecipeSource(kind=SourceKind.URL, url="https://www.daringgourmet.com/"),
        ]
        recipe.steps = [Step(position=1, text="Mix teff and water.", passive_minutes=4320)]
        s.add(recipe)

    with db.session_scope(engine) as s:
        loaded = s.scalars(select(Recipe).where(Recipe.ref == "core-002")).one()
        assert loaded.status is RecipeStatus.DRAFT
        assert loaded.collection is Collection.CORE
        assert loaded.family is not None and loaded.family.name == "injera"
        assert [src.kind for src in loaded.sources] == [SourceKind.PDF, SourceKind.URL]
        assert loaded.steps[0].passive_minutes == 4320


def test_foreign_keys_are_enforced(db_url):
    from mealplan.models.enums import Meal
    from mealplan.models.tables import MealSlot

    db.upgrade(db_url)
    engine = db.make_engine(db_url)
    with pytest.raises(IntegrityError), db.session_scope(engine) as s:
        s.add(MealSlot(date=date(2026, 9, 28), meal=Meal.DINNER, recipe_id=999, servings=4))
