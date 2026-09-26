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
