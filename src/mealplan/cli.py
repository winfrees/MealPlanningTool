"""`mealctl`: the command-line interface (UI-1)."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mealplan import __version__, db
from mealplan.config import get_settings
from mealplan.models.manifest import load_manifest

app = typer.Typer(help="Household meal planner: plans, prep sessions, and shopping lists.")
db_app = typer.Typer(help="Database management.")
manifest_app = typer.Typer(help="Core recipe manifest.")
app.add_typer(db_app, name="db")
app.add_typer(manifest_app, name="manifest")

console = Console()


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"mealplan {__version__}")


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Create or migrate the SQLite database to the latest schema."""
    settings = get_settings()
    db.upgrade(settings.db_url, revision)
    console.print(f"Database at [bold]{settings.db_path}[/] is at revision {revision}.")


@db_app.command("revision")
def db_revision(message: Annotated[str, typer.Option("-m", "--message")]) -> None:
    """Autogenerate a migration from model changes (review it before committing)."""
    settings = get_settings()
    db.upgrade(settings.db_url)
    db.revision(settings.db_url, message)


@manifest_app.command("check")
def manifest_check(
    path: Annotated[Path | None, typer.Argument(help="Manifest JSON file.")] = None,
) -> None:
    """Validate the core recipe manifest and summarize it."""
    path = path or get_settings().data_dir / "core_recipe_manifest.json"
    manifest = load_manifest(path)

    roles: dict[str, int] = {}
    for r in manifest.recipes:
        roles[r.meal_role] = roles.get(r.meal_role, 0) + 1

    table = Table(title=f"{manifest.source_pdf}: {len(manifest.recipes)} recipes")
    table.add_column("Meal role")
    table.add_column("Recipes", justify="right")
    for role, count in sorted(roles.items(), key=lambda kv: -kv[1]):
        table.add_row(role, str(count))
    console.print(table)

    for name, members in sorted(manifest.families().items()):
        console.print(f"Family [bold]{name}[/]: " + ", ".join(m.id for m in members))
