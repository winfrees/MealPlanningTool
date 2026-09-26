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
