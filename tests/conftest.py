from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def manifest_path() -> Path:
    return REPO_ROOT / "data" / "core_recipe_manifest.json"


@pytest.fixture(scope="session")
def catalog():
    from mealplan.core.normalizer import Catalog

    return Catalog.from_csv(REPO_ROOT / "data" / "ingredients.csv")


@pytest.fixture
def session(db_url, catalog):
    """A migrated database with the ingredient catalog seeded."""
    from mealplan import db
    from mealplan.core.normalizer import seed_catalog

    db.upgrade(db_url)
    engine = db.make_engine(db_url)
    with db.session_scope(engine) as s:
        seed_catalog(s, catalog)
        yield s
