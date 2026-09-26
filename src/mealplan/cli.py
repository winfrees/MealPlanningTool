"""`mealctl`: the command-line interface (UI-1)."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan import __version__, db
from mealplan.config import get_settings
from mealplan.core import library, plan_store
from mealplan.core.base_week import rotation, seed_house_meals
from mealplan.core.components import load_components_csv, seed_components
from mealplan.core.library import LibraryError
from mealplan.core.normalizer import Catalog, seed_catalog
from mealplan.core.plan_store import PlanError
from mealplan.core.preferences import PrefsError, Weekday, load_prefs, set_pref
from mealplan.core.prep import build_prep
from mealplan.core.render import day_card, day_cards_markdown, plan_markdown, prep_markdown
from mealplan.core.scaling import scale_quantity
from mealplan.core.units import format_qty
from mealplan.ingest import review_queue
from mealplan.ingest.pdf import BudgetExceeded, import_manifest
from mealplan.models.enums import Collection, Meal, RecipeStatus
from mealplan.models.manifest import load_manifest
from mealplan.models.schemas import RecipeDraft
from mealplan.models.tables import IngestFailure, Recipe

app = typer.Typer(help="Household meal planner: plans, prep sessions, and shopping lists.")
db_app = typer.Typer(help="Database management.")
manifest_app = typer.Typer(help="Core recipe manifest.")
catalog_app = typer.Typer(help="Ingredient catalog.")
recipes_app = typer.Typer(help="Recipe library.")
review_app = typer.Typer(help="Review queue for imported drafts (ING-3).")
import_app = typer.Typer(help="Import recipes from the source PDF (ING-1).")
prefs_app = typer.Typer(help="Household planning rules (PLN-1).")
plan_app = typer.Typer(help="Weekly plan: dinners, lunches, day cards (PLN-1..8).")
prep_app = typer.Typer(help="Sunday prep checklist (PLN-5).")
app.add_typer(db_app, name="db")
app.add_typer(manifest_app, name="manifest")
app.add_typer(catalog_app, name="catalog")
app.add_typer(recipes_app, name="recipes")
app.add_typer(review_app, name="review")
app.add_typer(import_app, name="import")
app.add_typer(prefs_app, name="prefs")
app.add_typer(plan_app, name="plan")
app.add_typer(prep_app, name="prep")

console = Console()


@contextmanager
def _session() -> Iterator[Session]:
    settings = get_settings()
    db.upgrade(settings.db_url)
    try:
        with db.session_scope(db.make_engine(settings.db_url)) as s:
            yield s
    except (LibraryError, PlanError, PrefsError) as e:
        console.print(f"[red]{e}[/]", markup=True, highlight=False)
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
    components = load_components_csv(path.parent / "components.csv")
    with _session() as s:
        added, updated = seed_catalog(s, catalog)
        c_added, c_updated = seed_components(s, components)
        house = seed_house_meals(s, path.parent / "house_meals.json", Catalog.from_db(s))
    console.print(f"Catalog: {added} added, {updated} updated.")
    console.print(f"Prep components: {c_added} added, {c_updated} updated.")
    console.print(f"House meals added: {', '.join(house) or 'none (already there)'}.")


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


@import_app.command("pdf")
def import_pdf(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Source PDF.")],
    manifest: Annotated[Path | None, typer.Option(help="Recipe manifest JSON.")] = None,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="Manifest ids to import (repeatable).")
    ] = None,
    agent: Annotated[
        bool, typer.Option("--agent/--no-agent", help="Use the Claude extraction agent.")
    ] = True,
) -> None:
    """Import the core collection into the review queue: web prints first, then the agent."""
    from mealplan.agents.extractor import ClaudeExtractor

    settings = get_settings()
    manifest_data = load_manifest(manifest or settings.data_dir / "core_recipe_manifest.json")
    extractor = None
    if agent:
        import anthropic

        key = settings.anthropic_api_key
        extractor = ClaudeExtractor(
            anthropic.Anthropic(api_key=key.get_secret_value()) if key else anthropic.Anthropic()
        )
    with _session() as s:
        catalog = _catalog(s)
        try:
            report = import_manifest(
                s,
                pdf,
                manifest_data,
                catalog,
                extractor,
                only=set(only) if only else None,
                weekly_budget_usd=settings.agent_weekly_budget_usd,
            )
        except BudgetExceeded as e:
            console.print(f"[yellow]{e}[/] Recipes imported so far are kept.")
            return
    console.print(f"Created {len(report.created)} drafts: {' '.join(report.created)}")
    if report.skipped:
        console.print(f"Already in the library: {len(report.skipped)}")
    if report.needs_agent:
        console.print(f"Need the agent (--agent): {' '.join(report.needs_agent)}")
    if report.failed:
        console.print(f"[red]Failed[/] (see `mealctl import failures`): {' '.join(report.failed)}")
    console.print(f"Agent cost this run: ${report.cost_usd:.2f}")


@import_app.command("failures")
def import_failures() -> None:
    """Imports that failed validation, grounding, or the API after one retry."""
    with _session() as s:
        table = Table()
        for col in ("Ref", "Pages", "Stage", "Error"):
            table.add_column(col)
        for f in s.scalars(select(IngestFailure).order_by(IngestFailure.id)):
            table.add_row(f.ref, f.pages, f.stage, f.error[:120])
        console.print(table)


def _week_start(s: Session, start: date | None) -> date:
    return start or plan_store.next_prep_day(date.today(), load_prefs(s))


StartOption = Annotated[
    datetime | None,
    typer.Option("--start", formats=["%Y-%m-%d"], help="Prep day that starts the week."),
]


def _as_date(value: datetime | None) -> date | None:
    return value.date() if value else None


@prefs_app.command("show")
def prefs_show() -> None:
    """Show the household rules the planner uses."""
    with _session() as s:
        for key, value in load_prefs(s).model_dump(mode="json").items():
            typer.echo(f"{key}: {json.dumps(value)}")


@prefs_app.command("set")
def prefs_set(key: str, value: str) -> None:
    """Set a rule, e.g. `prefs set dinner_servings 4` or `prefs set lunch_days mon,tue,wed`."""
    with _session() as s:
        prefs = set_pref(s, key, value)
        typer.echo(f"{key}: {json.dumps(prefs.model_dump(mode='json')[key])}")


@plan_app.command("week")
def plan_week_cmd(
    start: StartOption = None,
    seed: Annotated[int | None, typer.Option(help="Seed; defaults to the stored one.")] = None,
    force: Annotated[bool, typer.Option(help="Re-plan a locked week.")] = False,
) -> None:
    """Plan (or re-plan) a week, keeping manual swaps. Same seed, same plan."""
    with _session() as s:
        week_start = _week_start(s, _as_date(start))
        result = plan_store.plan_and_save(s, week_start, load_prefs(s), seed=seed, force=force)
        typer.echo(plan_markdown(result))


@plan_app.command("show")
def plan_show(
    start: StartOption = None,
    day: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Show one day card.")
    ] = None,
) -> None:
    """Show a saved week, or one day's card."""
    with _session() as s:
        week_start = _week_start(s, _as_date(start))
        result = plan_store.saved_plan(s, week_start)
        if day is None:
            week = plan_store.get_week(s, week_start)
            typer.echo(plan_markdown(result, week.status if week else "draft"))
            return
        dishes = plan_store.plan_dishes(s, result)
        typer.echo("\n".join(day_card(day.date(), result, build_prep(result, dishes), dishes)))


@plan_app.command("cards")
def plan_cards(start: StartOption = None) -> None:
    """Day cards for the whole week: tonight's dinner and tomorrow's lunch (PLN-6)."""
    with _session() as s:
        result = plan_store.saved_plan(s, _week_start(s, _as_date(start)))
        dishes = plan_store.plan_dishes(s, result)
        typer.echo(day_cards_markdown(result, build_prep(result, dishes), dishes))


@plan_app.command("swap")
def plan_swap(
    day: Annotated[datetime, typer.Argument(formats=["%Y-%m-%d"])],
    meal: Meal,
    ref: str,
    start: StartOption = None,
) -> None:
    """Swap one meal; other dinners stay, leftovers and prep follow (PLN-7)."""
    with _session() as s:
        week_start = _week_start(s, _as_date(start))
        result = plan_store.swap(s, week_start, day.date(), meal, ref, load_prefs(s))
        typer.echo(plan_markdown(result))


@plan_app.command("lock")
def plan_lock(
    start: StartOption = None,
    unlock: Annotated[bool, typer.Option("--unlock", help="Unlock instead.")] = False,
) -> None:
    """Lock the week once it is final (the Friday step); unlock to change it."""
    with _session() as s:
        week = plan_store.set_locked(s, _week_start(s, _as_date(start)), not unlock)
        typer.echo(f"Week of {week.week_start} is {week.status}.")


@plan_app.command("cooked")
def plan_cooked(
    day: Annotated[datetime, typer.Argument(formats=["%Y-%m-%d"])],
    meal: Meal = Meal.DINNER,
) -> None:
    """Mark a meal as cooked."""
    with _session() as s:
        plan_store.mark_cooked(s, day.date(), meal)
        typer.echo(f"{meal} on {day.date()} marked cooked.")


@prep_app.command("show")
def prep_show(start: StartOption = None) -> None:
    """The prep-day checklist, in the order to start things."""
    with _session() as s:
        result = plan_store.saved_plan(s, _week_start(s, _as_date(start)))
        typer.echo(prep_markdown(build_prep(result, plan_store.plan_dishes(s, result))))


@plan_app.command("base")
def plan_base(
    day: Annotated[Weekday | None, typer.Argument(help="Day to change, e.g. tue.")] = None,
    meal: Annotated[
        str | None,
        typer.Argument(help='A recipe ref, refs to rotate ("house-002,house-003"), or "menu".'),
    ] = None,
) -> None:
    """Show or change the base week: standing dinners every plan starts from."""
    with _session() as s:
        prefs = load_prefs(s)
        base = dict(prefs.base_week)
        if day is not None:
            if meal is None:
                raise typer.BadParameter("give a recipe ref, refs to rotate, or 'menu'")
            if meal == "menu":
                base.pop(day, None)
            else:
                refs = [r.strip() for r in meal.replace("|", ",").split(",") if r.strip()]
                for ref in refs:
                    library.get_recipe(s, ref)  # must exist
                base[day] = "|".join(refs)
            prefs = set_pref(s, "base_week", json.dumps({d.value: v for d, v in base.items()}))
        titles = {r.ref: r.title for r in s.scalars(select(Recipe))}
        for weekday in Weekday:
            rule = prefs.base_week.get(weekday)
            if rule is None:
                typer.echo(f"{weekday.value}: menu (the planner chooses)")
                continue
            names = " / ".join(f"{titles.get(r, r)} ({r})" for r in rotation(rule))
            label = "alternates " if len(rotation(rule)) > 1 else ""
            typer.echo(f"{weekday.value}: {label}{names}")
