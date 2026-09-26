from typer.testing import CliRunner

from mealplan.cli import app

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "meal planner" in result.output


def test_db_upgrade_creates_database(tmp_path, monkeypatch):
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("MEALPLAN_DB_PATH", str(db_path))
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0, result.output
    assert db_path.exists()


def test_manifest_check_summarizes(manifest_path):
    result = runner.invoke(app, ["manifest", "check", str(manifest_path)])
    assert result.exit_code == 0, result.output
    assert "93 recipes" in result.output
    assert "injera" in result.output


def test_library_workflow_end_to_end(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "e2e.db"))
    from tests.conftest import REPO_ROOT

    monkeypatch.setenv("MEALPLAN_DATA_DIR", str(REPO_ROOT / "data"))

    result = runner.invoke(app, ["recipes", "list"])
    assert result.exit_code == 0

    draft = {
        "title": "Doro Wat",
        "servings": 4,
        "ingredients": [
            {"raw_text": "2 lb chicken drumsticks"},
            {"raw_text": "3 large eggs"},
            {"raw_text": "1/4 cup berbere spice"},
        ],
        "steps": [{"text": "Simmer the onions.", "active_minutes": 20}],
        "sources": [{"kind": "pdf", "file": "Recipes_12Sept26.pdf", "pages": [1, 2, 3]}],
    }
    path = tmp_path / "doro.json"
    path.write_text(json.dumps(draft))

    assert runner.invoke(app, ["recipes", "add", str(path)]).exit_code == 1  # no catalog yet
    result = runner.invoke(app, ["catalog", "seed"])
    assert result.exit_code == 0, result.output
    assert "added" in result.output

    result = runner.invoke(app, ["recipes", "add", str(path)])
    assert result.exit_code == 0, result.output
    assert "core-001" in result.output

    result = runner.invoke(app, ["review", "list"])
    assert "core-001" in result.output

    result = runner.invoke(app, ["review", "show", "core-001"])
    assert result.exit_code == 0, result.output
    assert "Issues" in result.output

    assert runner.invoke(app, ["review", "approve", "core-001"]).exit_code == 0
    result = runner.invoke(app, ["review", "approve", "core-001"])
    assert result.exit_code == 1 and "already approved" in result.output

    result = runner.invoke(app, ["recipes", "show", "core-001", "--servings", "2"])
    assert result.exit_code == 0, result.output
    assert "Serves 2" in result.output
    assert "1 lb chicken drumstick" in result.output
    assert "2 egg" in result.output  # 1.5 eggs rounds to a whole egg
    assert "2 tbsp berbere" in result.output

    result = runner.invoke(app, ["rate", "core-001", "5", "--repeat"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["recipes", "list", "--status", "approved"])
    assert "Doro Wat" in result.output


def test_import_pdf_without_agent(tmp_path, monkeypatch):
    import json

    from tests.conftest import REPO_ROOT
    from tests.test_pdf_import import MANIFEST, make_pdf

    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "imp.db"))
    monkeypatch.setenv("MEALPLAN_DATA_DIR", str(REPO_ROOT / "data"))
    pdf = make_pdf(tmp_path / "Recipes_12Sept26.pdf")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST.model_dump()))

    assert runner.invoke(app, ["catalog", "seed"]).exit_code == 0
    result = runner.invoke(
        app, ["import", "pdf", str(pdf), "--manifest", str(manifest), "--no-agent"]
    )
    assert result.exit_code == 0, result.output
    assert "Created 1 drafts: core-024" in result.output
    assert "Need the agent" in result.output and "core-049" in result.output

    result = runner.invoke(app, ["import", "failures"])
    assert result.exit_code == 0, result.output


def test_planning_workflow_end_to_end(tmp_path, monkeypatch, catalog):
    from mealplan import db
    from tests.conftest import REPO_ROOT
    from tests.planning_fixtures import GOLDEN_WEEK, populate_golden

    db_path = tmp_path / "plan.db"
    monkeypatch.setenv("MEALPLAN_DB_PATH", str(db_path))
    monkeypatch.setenv("MEALPLAN_DATA_DIR", str(REPO_ROOT / "data"))
    assert runner.invoke(app, ["catalog", "seed"]).exit_code == 0
    url = f"sqlite:///{db_path}"
    with db.session_scope(db.make_engine(url)) as s:
        populate_golden(s, catalog)

    result = runner.invoke(app, ["prefs", "show"])
    assert "dinner_servings: 4" in result.output

    result = runner.invoke(app, ["plan", "base"])
    assert result.exit_code == 0, result.output
    assert "mon: Salmon, Jasmine Rice and Broccoli (house-001)" in result.output
    assert (
        "tue: alternates Tacos (Refried Beans) (house-002) / Tacos (Meat) (house-003)"
        in result.output
    )
    assert "wed: menu (the planner chooses)" in result.output
    result = runner.invoke(app, ["plan", "base", "sun", "core-044"])
    assert "sun: Jalapeno-Orange Pork Tenderloin with Snap Peas (core-044)" in result.output
    result = runner.invoke(app, ["plan", "base", "sun", "menu"])
    assert "sun: menu" in result.output
    assert runner.invoke(app, ["plan", "base", "sun", "core-999"]).exit_code == 1
    assert runner.invoke(app, ["prefs", "set", "max_spice", "9"]).exit_code == 1

    start = ["--start", "2026-10-04"]
    result = runner.invoke(app, ["plan", "week", *start, "--seed", "7"])
    assert result.exit_code == 0, result.output
    assert result.output == (GOLDEN_WEEK / "plan.md").read_text() + "\n"

    result = runner.invoke(app, ["plan", "cards", *start])
    assert result.exit_code == 0, result.output
    assert result.output == (GOLDEN_WEEK / "daycards.md").read_text() + "\n"

    result = runner.invoke(app, ["prep", "show", *start])
    assert result.output == (GOLDEN_WEEK / "prep.md").read_text() + "\n"

    result = runner.invoke(app, ["plan", "show", *start, "--day", "2026-10-09"])
    assert "Pizza Night (order in)" in result.output
    assert "Order in: nothing to cook" in result.output

    result = runner.invoke(app, ["plan", "swap", "2026-10-06", "dinner", "core-048", *start])
    assert result.exit_code == 0, result.output
    assert "The Best Black Bean Burgers (core-048) · 6 servings, swapped in" in result.output
    assert result.output.count("swapped in") == 1

    assert runner.invoke(app, ["plan", "lock", *start]).exit_code == 0
    result = runner.invoke(app, ["plan", "week", *start])
    assert result.exit_code == 1 and "locked" in result.output
    assert runner.invoke(app, ["plan", "lock", *start, "--unlock"]).exit_code == 0

    result = runner.invoke(app, ["plan", "cooked", "2026-10-04"])
    assert result.exit_code == 0, result.output
