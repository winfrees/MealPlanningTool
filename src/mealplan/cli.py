"""`mealctl`: the command-line interface (UI-1)."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan import __version__, db
from mealplan.config import get_settings
from mealplan.core import library
from mealplan.core.library import LibraryError
from mealplan.core.normalizer import Catalog, seed_catalog
from mealplan.core.scaling import scale_quantity
from mealplan.core.units import format_qty
from mealplan.ingest import review_queue
from mealplan.models.enums import Collection, RecipeStatus
from mealplan.models.manifest import load_manifest
from mealplan.models.schemas import RecipeDraft
from mealplan.models.tables import Recipe

app = typer.Typer(help="Household meal planner: plans, prep sessions, and shopping lists.")
db_app = typer.Typer(help="Database management.")
manifest_app = typer.Typer(help="Core recipe manifest.")
catalog_app = typer.Typer(help="Ingredient catalog.")
recipes_app = typer.Typer(help="Recipe library.")
review_app = typer.Typer(help="Review queue for imported drafts (ING-3).")
app.add_typer(db_app, name="db")
app.add_typer(manifest_app, name="manifest")
app.add_typer(catalog_app, name="catalog")
app.add_typer(recipes_app, name="recipes")
app.add_typer(review_app, name="review")

console = Console()


@contextmanager
def _session() -> Iterator[Session]:
    settings = get_settings()
    db.upgrade(settings.db_url)
    try:
        with db.session_scope(db.make_engine(settings.db_url)) as s:
            yield s
    except LibraryError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None


def _catalog(session: Session) -> Catalog:
    catalog = Catalog.from_db(session)
    if not len(catalog):
        console.print("[red]Ingredient catalog is empty; run `mealctl catalog seed` first.[/]")
        raise typer.Exit(1)
    return catalog


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


@catalog_app.command("seed")
def catalog_seed(
    path: Annotated[Path | None, typer.Argument(help="Catalog CSV.")] = None,
) -> None:
    """Load or refresh the ingredient catalog from CSV."""
    path = path or get_settings().data_dir / "ingredients.csv"
    catalog = Catalog.from_csv(path)
    with _session() as s:
        added, updated = seed_catalog(s, catalog)
    console.print(f"Catalog: {added} added, {updated} updated.")


@recipes_app.command("add")
def recipes_add(
    path: Annotated[Path, typer.Argument(help="RecipeDraft JSON file (one object or a list).")],
) -> None:
    """Add recipes from JSON (manual entry); they land in the review queue as drafts."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    drafts = [RecipeDraft.model_validate(d) for d in (raw if isinstance(raw, list) else [raw])]
    with _session() as s:
        catalog = _catalog(s)
        for d in drafts:
            r = library.create_draft(s, d, catalog)
            console.print(f"Draft [bold]{r.ref}[/] {r.title}")


@recipes_app.command("list")
def recipes_list(
    status: RecipeStatus | None = None,
    collection: Collection | None = None,
) -> None:
    """List recipes."""
    with _session() as s:
        query = select(Recipe).order_by(Recipe.ref)
        if status:
            query = query.where(Recipe.status == status)
        if collection:
            query = query.where(Recipe.collection == collection)
        table = Table()
        for col in ("Ref", "Title", "Role", "Status", "Family", "Tags"):
            table.add_column(col)
        for r in s.scalars(query):
            table.add_row(
                r.ref,
                r.title,
                r.meal_role or "",
                r.status,
                r.family.name if r.family else "",
                ", ".join(r.tags),
            )
        console.print(table)


@recipes_app.command("show")
def recipes_show(
    ref: str,
    servings: Annotated[float | None, typer.Option(help="Scale to this many servings.")] = None,
) -> None:
    """Show a recipe, optionally scaled (REC-6)."""
    with _session() as s:
        r = library.get_recipe(s, ref)
        factor = 1.0
        if servings is not None:
            if not r.servings:
                console.print("[red]Recipe has no serving count; cannot scale.[/]")
                raise typer.Exit(1)
            factor = servings / r.servings
        shown = servings or r.servings
        console.print(f"[bold]{r.title}[/] ({r.ref}, {r.status}, {r.collection})")
        if shown:
            console.print(f"Serves {format_qty(shown)}")
        for src in r.sources:
            where = src.url or f"{src.file} p{src.pages}"
            console.print(f"[dim]Source: {src.title or src.kind} {where}[/]")
        console.print("\n[bold]Ingredients[/]")
        for ing in r.ingredients:
            name = ing.ingredient.canonical_name if ing.ingredient else ing.raw_text
            if ing.qty is None:
                console.print(f"  {ing.raw_text}")
                continue
            qty, unit = (
                scale_quantity(ing.qty, ing.unit, factor, name)
                if factor != 1
                else (
                    ing.qty,
                    ing.unit,
                )
            )
            note = f", {ing.prep_note}" if ing.prep_note else ""
            console.print(f"  {format_qty(qty)} {unit or ''} {name}{note}".replace("  ", " "))
        console.print("\n[bold]Steps[/]")
        for st in r.steps:
            console.print(f"  {st.position}. {st.text}")


@app.command()
def rate(
    ref: str,
    score: Annotated[int, typer.Argument(min=1, max=5)],
    notes: str = "",
    repeat: Annotated[bool | None, typer.Option("--repeat/--no-repeat")] = None,
) -> None:
    """Rate a recipe after eating it (REC-5); discovered recipes rated 4+ become core."""
    with _session() as s:
        r = library.get_recipe(s, ref)
        library.rate(s, r, score, date.today(), would_repeat=repeat, notes=notes)
        console.print(f"Rated {r.ref} {score}/5 ({r.collection}).")


@review_app.command("list")
def review_list() -> None:
    """Drafts waiting for review."""
    with _session() as s:
        table = Table()
        for col in ("Ref", "Title", "Issues", "Similar"):
            table.add_column(col)
        for r in review_queue.pending(s):
            item = review_queue.review(s, r)
            table.add_row(
                r.ref,
                r.title,
                str(len(item.issues)),
                ", ".join(f"{m.recipe.ref} ({m.score:.2f})" for m in item.similar),
            )
        console.print(table)


@review_app.command("show")
def review_show(ref: str) -> None:
    """A draft with its issues and similar recipes (copy or variant?)."""
    recipes_show(ref)
    with _session() as s:
        item = review_queue.review(s, library.get_recipe(s, ref))
        console.print("\n[bold]Issues[/]")
        for issue in item.issues or ["none"]:
            console.print(f"  - {issue}")
        if item.similar:
            console.print("\n[bold]Similar recipes[/] (merge a copy, or add to a family)")
            for m in item.similar:
                console.print(f"  {m.recipe.ref} {m.recipe.title}: {m.score:.2f}")


@review_app.command("approve")
def review_approve(refs: list[str]) -> None:
    """Approve drafts (a person's action)."""
    with _session() as s:
        for ref in refs:
            review_queue.approve(s, library.get_recipe(s, ref))
            console.print(f"Approved {ref}.")


@review_app.command("reject")
def review_reject(refs: list[str]) -> None:
    """Delete drafts."""
    with _session() as s:
        for ref in refs:
            review_queue.reject(s, library.get_recipe(s, ref))
            console.print(f"Rejected {ref}.")


@review_app.command("merge")
def review_merge(draft_ref: str, into_ref: str) -> None:
    """Collapse a draft that is a copy of an existing recipe into it (REC-3)."""
    with _session() as s:
        target = library.merge_copy(
            s, library.get_recipe(s, draft_ref), library.get_recipe(s, into_ref)
        )
        console.print(f"Merged {draft_ref} into {target.ref}; {len(target.sources)} sources.")


@review_app.command("family")
def review_family(ref: str, name: str) -> None:
    """Put a recipe in a variant family (REC-9)."""
    with _session() as s:
        library.add_to_family(s, library.get_recipe(s, ref), name)
        console.print(f"{ref} is in family {name!r}.")
