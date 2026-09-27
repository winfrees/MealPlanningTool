"""First run (UI-8): reference data, the demo household, the password, and importing the
collection from the browser."""

import shutil
import stat
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mealplan import db
from mealplan.agents.extractor import RecipeExtractor
from mealplan.config import Settings
from mealplan.core import library, plan_store, setup
from mealplan.core.normalizer import Catalog
from mealplan.core.preferences import load_prefs
from mealplan.models.enums import RecipeStatus
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef
from mealplan.models.tables import Ingredient, Recipe
from mealplan.web import auth, launch
from mealplan.web.app import create_app
from tests.conftest import REPO_ROOT
from tests.test_pdf_import import MANIFEST, FakeExtractor, make_pdf

DATA = REPO_ROOT / "data"
TODAY = date(2026, 10, 3)
HEADERS = {auth.CSRF_HEADER: "1"}


def fresh(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "home.db", web_password=SecretStr("correct horse"))


def test_prepare_loads_reference_data_once(tmp_path):
    settings = fresh(tmp_path)
    db.upgrade(settings.db_url)
    engine = db.make_engine(settings.db_url)
    with db.session_scope(engine) as s:
        assert setup.prepare(s, DATA) is True
        count = s.scalar(select(func.count()).select_from(Ingredient))
        assert count and count > 100
        house = s.scalars(select(Recipe.ref).where(Recipe.ref.like("house-%"))).all()
        assert house
    with db.session_scope(engine) as s:
        assert setup.prepare(s, DATA) is False
        # House meals are not the household's library: a new install has no recipes yet.
        assert setup.library_status(s) == setup.LibraryStatus(approved=0, drafts=0)


def draft(session: Session, catalog: Catalog, ref: str, lines: list[str]) -> Recipe:
    return library.create_draft(
        session,
        RecipeDraft(
            title=ref.title(),
            ingredients=[IngredientLine(raw_text=line) for line in lines],
            steps=[{"text": "Cook."}],
            sources=[SourceRef(kind="manual")],
        ),
        catalog,
        ref=ref,
    )


def test_approve_ready_skips_drafts_with_issues(session, catalog):
    draft(session, catalog, "clean", ["2 lb chicken thighs", "1 cup rice"])
    draft(session, catalog, "messy", ["2 lb chicken thighs", "1 cup unobtainium"])
    assert setup.approve_ready(session) == ["clean"]
    assert library.get_recipe(session, "clean").status is RecipeStatus.APPROVED
    assert library.get_recipe(session, "messy").status is RecipeStatus.DRAFT


def test_demo_household_has_a_planned_week(tmp_path):
    real = fresh(tmp_path)
    demo = launch.demo_settings(real, TODAY)
    assert demo.db_path == tmp_path / "demo.db"
    assert demo.web_password is not None
    assert demo.web_password.get_secret_value() == launch.DEMO_PASSWORD
    assert not real.db_path.exists()  # the real database is untouched
    with db.session_scope(db.make_engine(demo.db_url)) as s:
        assert setup.library_status(s).approved > 20
        start = plan_store.next_prep_day(TODAY, load_prefs(s))
        assert plan_store.get_week(s, start) is not None
        recipes = s.scalar(select(func.count()).select_from(Recipe))
    # Every demo run starts again from the same sample household.
    launch.demo_settings(real, TODAY)
    with db.session_scope(db.make_engine(demo.db_url)) as s:
        assert s.scalar(select(func.count()).select_from(Recipe)) == recipes


def test_save_password_keeps_other_settings_private(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=abc\nMEALPLAN_WEB_PASSWORD=old-password\n")
    launch.save_password(env, "new household pass")
    assert env.read_text() == "ANTHROPIC_API_KEY=abc\nMEALPLAN_WEB_PASSWORD=new household pass\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="at least"):
        launch.save_password(env, "short")
    with pytest.raises(ValueError, match="one line"):
        launch.save_password(env, "two\nlines here")


def test_saved_password_is_read_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEALPLAN_WEB_PASSWORD", raising=False)
    launch.save_password(Path(".env"), "our kitchen 2026")
    settings = Settings()
    assert settings.web_password is not None
    assert settings.web_password.get_secret_value() == "our kitchen 2026"


# --- the web app on a new install ---------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """The repository's reference data with a three-recipe collection (tests.test_pdf_import)."""
    data = tmp_path / "data"
    data.mkdir()
    for name in ("ingredients.csv", "components.csv", "house_meals.json"):
        shutil.copy(DATA / name, data / name)
    (data / "core_recipe_manifest.json").write_text(MANIFEST.model_dump_json())
    return data


def new_install(
    tmp_path: Path, data_dir: Path, extractor: RecipeExtractor | None = None
) -> TestClient:
    settings = fresh(tmp_path).model_copy(update={"data_dir": data_dir})
    app = create_app(settings, today=lambda: TODAY, extractor=lambda: extractor)
    client = TestClient(app, headers=HEADERS)
    assert client.post("/api/login", json={"password": "correct horse"}).status_code == 200
    return client


@pytest.fixture
def client(tmp_path: Path, data_dir: Path) -> Iterator[TestClient]:
    with new_install(tmp_path, data_dir) as c:
        yield c


def wait_for_import(client: TestClient) -> dict[str, Any]:
    for _ in range(200):
        status: dict[str, Any] = client.get("/api/setup").json()
        if status["import"]["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("import did not finish")


def test_new_install_needs_no_setup_commands(client):
    status = client.get("/api/setup").json()
    assert status["approved"] == 0 and status["drafts"] == 0
    assert status["pdf_name"] == "Recipes_12Sept26.pdf" and not status["pdf_on_disk"]
    assert status["import"]["state"] == "idle"
    assert len(client.get("/api/catalog").json()) > 100


def test_upload_imports_web_prints_without_an_api_key(client, tmp_path, data_dir):
    pdf = make_pdf(tmp_path / "upload.pdf").read_bytes()
    response = client.post(
        "/api/setup/pdf", content=pdf, headers={**HEADERS, "Content-Type": "application/pdf"}
    )
    assert response.status_code == 200, response.text
    status = wait_for_import(client)
    job = status["import"]
    assert (job["state"], job["created"], job["needs_agent"], job["uses_agent"]) == (
        "done",
        1,
        2,
        False,
    )
    assert status["drafts"] == 1 and status["pdf_on_disk"]
    assert (data_dir / "source" / "Recipes_12Sept26.pdf").read_bytes() == pdf

    approved = client.post("/api/review/approve-ready").json()["approved"]
    assert client.get("/api/setup").json()["approved"] == len(approved)


def test_import_uses_the_agent_when_configured(tmp_path, data_dir):
    (data_dir / "source").mkdir()
    make_pdf(data_dir / "source" / "Recipes_12Sept26.pdf")
    fake = FakeExtractor()
    with new_install(tmp_path, data_dir, extractor=fake) as c:
        assert c.post("/api/setup/import").status_code == 200
        job = wait_for_import(c)["import"]
    assert (job["state"], job["created"], job["needs_agent"], job["uses_agent"]) == (
        "done",
        3,
        0,
        True,
    )
    assert len(fake.requests) == 2
    # Importing again skips what is already there.
    with new_install(tmp_path, data_dir, extractor=fake) as c:
        c.post("/api/setup/import")
        assert wait_for_import(c)["import"]["skipped"] == 3


def test_upload_refuses_other_files(client, tmp_path, data_dir):
    pdf_type = {**HEADERS, "Content-Type": "application/pdf"}
    not_pdf = client.post("/api/setup/pdf", content=b"hello", headers=pdf_type)
    assert not_pdf.status_code == 400 and "not a PDF" in not_pdf.json()["detail"]

    other = tmp_path / "other.pdf"
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    doc.new_page()
    doc.save(other)  # type: ignore[no-untyped-call]
    wrong = client.post("/api/setup/pdf", content=other.read_bytes(), headers=pdf_type)
    assert wrong.status_code == 400 and "has 1 pages" in wrong.json()["detail"]
    assert not (data_dir / "source" / "Recipes_12Sept26.pdf").exists()
    assert not list((data_dir / "source").glob("*.upload"))


def test_import_without_a_saved_pdf_says_to_upload(client):
    response = client.post("/api/setup/import")
    assert response.status_code == 404 and "upload" in response.json()["detail"]


def test_setup_needs_login_and_csrf(tmp_path, data_dir):
    settings = fresh(tmp_path).model_copy(update={"data_dir": data_dir})
    with TestClient(create_app(settings, today=lambda: TODAY)) as c:
        assert c.get("/api/setup").status_code == 401
        assert c.post("/api/setup/pdf", content=b"%PDF-").status_code == 403


def test_catalog_is_ready_for_the_cli_too(tmp_path, data_dir):
    new_install(tmp_path, data_dir).close()
    settings = fresh(tmp_path)
    with db.session_scope(db.make_engine(settings.db_url)) as s:
        assert len(Catalog.from_db(s)) > 100


def test_serve_without_a_terminal_explains_the_password(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mealplan.cli import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEALPLAN_WEB_PASSWORD", raising=False)
    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "home.db"))
    result = CliRunner().invoke(app, ["serve", "--no-open"])
    assert result.exit_code == 1
    assert "No household password yet" in result.output
    assert not (tmp_path / ".env").exists()
