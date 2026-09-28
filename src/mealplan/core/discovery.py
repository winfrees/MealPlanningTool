"""The check every web find goes through before a person sees it (M5; REC-7, REC-8, §5).

Whoever proposes a link (a pasted URL, the scout agent, a Claude chat reply), the core fetches
the page itself, parses its structured recipe data, and checks the result against the
household's rules and library. Nothing here trusts what an agent said about the recipe.
Verdicts:

- `exists`: this page is already a source of a recipe in the library;
- `unreadable`: the page could not be fetched or has no structured recipe;
- `blocked`: breaks a hard rule (avoided tag or ingredient, too spicy), with the reasons;
- `duplicate`: looks like a recipe already in the library (REC-8: offered as a variant);
- `ok`: none of the above.

Adding a candidate creates a discovered draft in the review queue (ING-3); nothing is approved
here.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.agents.scout import AGENT_NAME as SCOUT_AGENT
from mealplan.agents.scout import RecipeScout
from mealplan.core import library, recipe_facts
from mealplan.core.normalizer import Catalog
from mealplan.core.parser import parse_ingredient
from mealplan.core.preferences import HouseholdPrefs
from mealplan.core.recipe_facts import Dish, DishIngredient, StepTime
from mealplan.ingest.fetch import Fetcher, FetchError, Page, fetch_page
from mealplan.ingest.pdf import BudgetExceeded, log_calls, spent_since
from mealplan.ingest.url import parse_recipes_html, to_draft
from mealplan.models.enums import Collection, MealRole
from mealplan.models.schemas import RecipeDraft
from mealplan.models.tables import Recipe, RecipeSource

MAX_FETCH_WORKERS = 4
VERDICTS = ("ok", "duplicate", "blocked", "exists", "unreadable")


@dataclass
class Candidate:
    url: str
    verdict: str  # ok | blocked | duplicate | exists | unreadable
    title: str = ""
    site: str = ""
    note: str = ""  # why the scout or chat suggested it (its words, shown as such)
    reasons: list[str] = field(default_factory=list)
    similar: list[tuple[str, str, float]] = field(default_factory=list)  # ref, title, score
    facts: dict[str, object] = field(default_factory=dict)
    draft: RecipeDraft | None = None


def normalize_url(url: str) -> str:
    """Scheme-less, lower-case host without "www.", path without a trailing slash, no query."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}"


def known_urls(session: Session) -> set[str]:
    return {normalize_url(u) for u in session.scalars(select(RecipeSource.url)) if u}


def dish_from_draft(draft: RecipeDraft, catalog: Catalog) -> Dish:
    ingredients = []
    for line in draft.ingredients:
        parsed = parse_ingredient(line.raw_text)
        match = catalog.match(parsed.name)
        ingredients.append(
            DishIngredient(
                match.entry.canonical_name if match else line.raw_text,
                parsed.qty,
                parsed.unit,
                parsed.optional,
                matched=match is not None,
            )
        )
    return Dish(
        ref="(web)",
        title=draft.title,
        role=draft.meal_role,
        servings=draft.servings,
        collection=Collection.DISCOVERED,
        tags=frozenset(draft.tags),
        ingredients=tuple(ingredients),
        steps=tuple(
            StepTime(s.text, s.active_minutes, s.passive_minutes, tuple(s.equipment))
            for s in draft.steps
        ),
        prep_minutes=draft.prep_minutes,
        cook_minutes=draft.cook_minutes,
    )


def _facts(dish: Dish, draft: RecipeDraft, prefs: HouseholdPrefs) -> dict[str, object]:
    active = recipe_facts.active_minutes(dish)
    return {
        "protein": recipe_facts.protein(dish),
        "active_minutes": active,
        "total_minutes": draft.total_minutes,
        "servings": draft.servings,
        "role": draft.meal_role.value if draft.meal_role else None,
        "spice": recipe_facts.spice_level(dish),
        "weeknight_ok": None if active is None else active <= prefs.max_weeknight_active_minutes,
        "leftover_friendly": recipe_facts.leftover_friendly(dish, prefs.leftover_exclude_proteins),
        "ingredients": len(draft.ingredients),
        "steps": len(draft.steps),
        "unmatched": sum(1 for i in dish.ingredients if not i.matched),
    }


def check_page(
    session: Session,
    prefs: HouseholdPrefs,
    catalog: Catalog,
    page: Page,
    role: MealRole | None = None,
    note: str = "",
    known: set[str] | None = None,
) -> Candidate:
    known = known_urls(session) if known is None else known
    if normalize_url(page.url) in known or normalize_url(page.final_url) in known:
        return Candidate(page.url, "exists", note=note, reasons=["already in your recipes"])
    recipes = [r for r in parse_recipes_html(page.html) if r.ingredients]
    if not recipes:
        return Candidate(
            page.url, "unreadable", note=note, reasons=["no structured recipe on this page"]
        )
    parsed = recipes[0]
    draft = to_draft(parsed, page.final_url, role)
    dish = dish_from_draft(draft, catalog)
    candidate = Candidate(
        page.url,
        "ok",
        title=draft.title,
        site=parsed.site_name or (urlsplit(page.final_url).hostname or ""),
        note=note,
        facts=_facts(dish, draft, prefs),
        draft=draft,
    )
    if reasons := recipe_facts.violations(dish, prefs):
        candidate.verdict, candidate.reasons = "blocked", reasons
        return candidate
    names = frozenset(i.name for i in dish.ingredients if i.matched)
    similar = library.similar_to_draft(session, draft.title, names)
    if similar:
        candidate.verdict = "duplicate"
        candidate.similar = [(s.recipe.ref, s.recipe.title, s.score) for s in similar[:3]]
        top = similar[0].recipe
        candidate.reasons = [f"looks like {top.ref} {top.title}"]
    return candidate


def fetch_all(urls: list[str], fetcher: Fetcher) -> dict[str, Page | FetchError]:
    """Fetch several pages at once (network only; checks happen afterwards in one session)."""

    def one(url: str) -> Page | FetchError:
        try:
            return fetcher(url)
        except FetchError as e:
            return e

    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as pool:
        return dict(zip(urls, pool.map(one, urls), strict=True))


def check_urls(
    session: Session,
    prefs: HouseholdPrefs,
    catalog: Catalog,
    urls: list[str],
    fetcher: Fetcher | None = None,
    role: MealRole | None = None,
    notes: dict[str, str] | None = None,
) -> list[Candidate]:
    """Fetch and check each URL (duplicates in the list are checked once), in order."""
    unique = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    known = known_urls(session)
    pages = fetch_all(unique, fetcher or fetch_page)
    out = []
    for url in unique:
        note = (notes or {}).get(url, "")
        got = pages[url]
        if isinstance(got, FetchError):
            out.append(Candidate(url, "unreadable", note=note, reasons=[str(got)]))
        else:
            out.append(check_page(session, prefs, catalog, got, role, note, known))
    return out


class DiscoveryError(ValueError):
    pass


def add_candidate(
    session: Session,
    catalog: Catalog,
    candidate: Candidate,
    variant_of: str | None = None,
) -> Recipe:
    """A discovered draft in the review queue; `variant_of` puts it in that recipe's family
    (REC-8: a near-duplicate of core is a variant, never a separate dish or a replacement)."""
    if candidate.draft is None or candidate.verdict in ("blocked", "exists", "unreadable"):
        raise DiscoveryError(f"{candidate.url} can't be added: {'; '.join(candidate.reasons)}")
    recipe = library.create_draft(session, candidate.draft, catalog)
    if variant_of:
        target = library.get_recipe(session, variant_of)
        name = target.family.name if target.family else target.title.lower()
        if target.family is None:
            library.add_to_family(session, target, name)
        library.add_to_family(session, recipe, name)
    return recipe


# --- the scout (agent proposes, core disposes) ---------------------------------------------------

LIBRARY_SEARCH_LIMIT = 8


def library_search(session: Session, query: str) -> str:
    """The scout's read-only view of the library: recipes whose title or ingredients share a
    word with the query, best first."""
    words = library.title_tokens(query) or frozenset(query.lower().split())
    scored = []
    for r in session.scalars(select(Recipe).order_by(Recipe.ref)):
        names = " ".join(i.ingredient.canonical_name for i in r.ingredients if i.ingredient)
        title_hits = len(words & library.title_tokens(r.title))
        ingredient_hits = sum(1 for w in words if w in names)
        if title_hits or ingredient_hits:
            scored.append((-(2 * title_hits + ingredient_hits), r.ref, r))
    if not scored:
        return "no matches"
    lines = []
    for _, _, r in sorted(scored)[:LIBRARY_SEARCH_LIMIT]:
        role = r.meal_role.value if r.meal_role else "recipe"
        lines.append(f"{r.ref}: {r.title} ({role}, {r.collection.value})")
    return "\n".join(lines)


def scout_candidates(
    session: Session,
    prefs: HouseholdPrefs,
    catalog: Catalog,
    scout: RecipeScout,
    request_text: str,
    role: MealRole | None = None,
    fetcher: Fetcher | None = None,
    weekly_budget_usd: float = 10.0,
    now: datetime | None = None,
) -> list[Candidate]:
    """Ask the scout for links, log its calls (NFR-7), then check every link like a pasted one."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    if spent_since(session, now - timedelta(days=7)) >= weekly_budget_usd:
        raise BudgetExceeded(f"weekly agent budget of ${weekly_budget_usd:.2f} reached")
    result = scout.scout(request_text, lambda q: library_search(session, q))
    log_calls(session, result.calls, SCOUT_AGENT)
    session.flush()
    if result.failure is not None:
        raise DiscoveryError(
            f"the scout could not finish ({result.failure.stage}): {result.failure.error[:300]}"
        )
    notes = {s.url: s.reason for s in result.suggestions}
    return check_urls(
        session, prefs, catalog, [s.url for s in result.suggestions], fetcher, role, notes
    )
