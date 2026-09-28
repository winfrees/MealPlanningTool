"""ING-2: recipes from web pages with no agent, via schema.org Recipe JSON-LD.

Most recipe sites publish the recipe as JSON-LD for search engines. This finds every
`<script type="application/ld+json">` block, walks it (top level, lists, `@graph`,
`mainEntity`) for objects whose `@type` includes Recipe, and maps the fields the planner needs.
Pure and deterministic: fetching lives in `ingest/fetch.py`. Pages without structured data
return nothing; the caller decides whether an agent may read the page instead.
"""

import html as html_lib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from mealplan.models.enums import Collection, MealRole, SourceKind
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef, StepDraft


@dataclass(frozen=True)
class ParsedRecipe:
    title: str
    servings: float | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    ingredients: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    cuisines: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    author: str = ""
    site_name: str = ""


class _Scripts(HTMLParser):
    """Collects JSON-LD script bodies and the og:site_name meta tag."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self.site_name = ""
        self._in_ld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower().startswith("application/ld+json"):
            self._in_ld, self._buf = True, []
        elif tag == "meta" and a.get("property", "").lower() == "og:site_name":
            self.site_name = self.site_name or _clean(a.get("content", ""))

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._buf.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._in_ld:
            self._buf.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._in_ld:
            self._buf.append(f"&#{name};")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self.blocks.append("".join(self._buf))
            self._in_ld = False


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _clean(text: Any) -> str:
    """Unescape entities (twice: sites double-encode), drop HTML tags, collapse spaces."""
    s = html_lib.unescape(html_lib.unescape(str(text)))
    return _SPACE.sub(" ", _TAG.sub("", s)).strip()


def _types(node: dict[str, Any]) -> set[str]:
    t = node.get("@type", [])
    values = t if isinstance(t, list) else [t]
    return {str(v).rsplit("/", 1)[-1].lower() for v in values}


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    """Every dict in a JSON-LD document, depth first, in document order."""
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement"):
            if key in node:
                yield from _walk(node[key])


_ISO = re.compile(
    r"^P(?:(?P<d>\d+(?:\.\d+)?)D)?(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<s>\d+(?:\.\d+)?)S)?)?$",
    re.IGNORECASE,
)


def parse_iso_duration(text: str | None) -> int | None:
    """ISO 8601 duration to whole minutes ("PT1H15M" -> 75); None when absent or zero."""
    if not text:
        return None
    m = _ISO.match(str(text).strip())
    if not m or not any(m.groupdict().values()):
        return None
    parts = {k: float(v) if v else 0.0 for k, v in m.groupdict().items()}
    minutes = parts["d"] * 1440 + parts["h"] * 60 + parts["m"] + parts["s"] / 60
    return round(minutes) or None


_YIELD_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")
_NOT_SERVINGS = re.compile(
    r"\b(cups?|oz|ounces?|quarts?|pints?|liters?|litres?|ml|g|grams?|kg|lbs?|pounds?|"
    r"tablespoons?|tbsp|teaspoons?|tsp|gallons?)\b",
    re.IGNORECASE,
)


def parse_yield(value: Any) -> float | None:
    """Servings from recipeYield: a number, "4 servings", "Makes 12", or a list of those.
    A yield in a unit of measure ("1 cup", "12 oz") is not a serving count."""
    values = value if isinstance(value, list) else [value]
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, int | float):
            if v > 0:
                return float(v)
            continue
        text = _clean(v)
        if _NOT_SERVINGS.search(text):
            continue
        m = _YIELD_NUMBER.search(text)
        if m and float(m.group(1)) > 0:
            return float(m.group(1))
    return None


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        if isinstance(v, dict):
            v = v.get("name") or v.get("text") or ""
        for part in str(v).split(",") if isinstance(v, str) and "," in v else [v]:
            text = _clean(part) if part is not None else ""
            if text:
                out.append(text)
    return out


def _steps(value: Any) -> list[str]:
    """recipeInstructions: a string (one step per line), strings, HowToStep, HowToSection."""
    if value is None:
        return []
    if isinstance(value, str):
        return [s for s in (_clean(line) for line in re.split(r"\n|<br\s*/?>", value)) if s]
    if isinstance(value, dict):
        if "itemListElement" in value:
            return _steps(value["itemListElement"])
        return _steps(value.get("text") or value.get("name") or "")
    if isinstance(value, list):
        return [s for item in value for s in _steps(item)]
    return []


def _person(value: Any) -> str:
    names = _strings(value)
    return names[0] if names else ""


def _site_from_graph(docs: list[Any]) -> str:
    for doc in docs:
        for node in _walk(doc):
            if _types(node) & {"organization", "website"} and node.get("name"):
                return _clean(node["name"])
    return ""


def _to_parsed(node: dict[str, Any], site_name: str) -> ParsedRecipe:
    keywords = node.get("keywords", [])
    return ParsedRecipe(
        title=_clean(node.get("name", "")),
        servings=parse_yield(node.get("recipeYield")),
        prep_minutes=parse_iso_duration(node.get("prepTime")),
        cook_minutes=parse_iso_duration(node.get("cookTime")),
        total_minutes=parse_iso_duration(node.get("totalTime")),
        ingredients=[s for s in (_clean(i) for i in node.get("recipeIngredient", []) or []) if s],
        steps=_steps(node.get("recipeInstructions")),
        categories=[_clean(c) for c in _list(node.get("recipeCategory")) if _clean(c)],
        cuisines=[_clean(c) for c in _list(node.get("recipeCuisine")) if _clean(c)],
        keywords=_strings(keywords) if keywords else [],
        author=_person(node.get("author")),
        site_name=site_name,
    )


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_recipes_html(page: str) -> list[ParsedRecipe]:
    """Every schema.org Recipe on the page, in document order (usually one)."""
    scripts = _Scripts()
    scripts.feed(page)
    scripts.close()
    docs = []
    for block in scripts.blocks:
        try:
            docs.append(json.loads(block.strip()))
        except json.JSONDecodeError:
            continue  # one broken block (common) must not hide a valid one
    site = scripts.site_name or _site_from_graph(docs)
    found = []
    for doc in docs:
        for node in _walk(doc):
            if "recipe" in _types(node) and node.get("name"):
                found.append(_to_parsed(node, site))
    return found


# --- to a draft -------------------------------------------------------------------------------

_ROLES = {
    "soup": MealRole.SOUP,
    "soups": MealRole.SOUP,
    "stew": MealRole.SOUP,
    "lunch": MealRole.LUNCH,
    "salad": MealRole.LUNCH,
    "dinner": MealRole.DINNER,
    "main": MealRole.DINNER,
    "main course": MealRole.DINNER,
    "main dish": MealRole.DINNER,
    "entree": MealRole.DINNER,
    "breakfast": MealRole.BREAKFAST,
    "brunch": MealRole.BREAKFAST,
    "side": MealRole.SIDE,
    "side dish": MealRole.SIDE,
    "dessert": MealRole.DESSERT,
    "bread": MealRole.BREAD,
    "snack": MealRole.SNACK,
    "sauce": MealRole.CONDIMENT,
    "condiment": MealRole.CONDIMENT,
    "dressing": MealRole.CONDIMENT,
}


def meal_role(recipe: ParsedRecipe) -> MealRole | None:
    """The first recipeCategory that names a meal role; None leaves it to the reviewer."""
    for c in recipe.categories:
        if role := _ROLES.get(c.lower().strip()):
            return role
    return None


def to_draft(recipe: ParsedRecipe, url: str, role: MealRole | None = None) -> RecipeDraft:
    """A discovered-recipe draft (REC-7) with the page as its source (ING-4)."""
    return RecipeDraft(
        title=recipe.title,
        servings=recipe.servings,
        prep_minutes=recipe.prep_minutes,
        cook_minutes=recipe.cook_minutes,
        total_minutes=recipe.total_minutes,
        meal_role=meal_role(recipe) or role,
        tags=["web"],
        collection=Collection.DISCOVERED,
        ingredients=[IngredientLine(raw_text=line) for line in recipe.ingredients],
        steps=[StepDraft(text=s) for s in recipe.steps],
        sources=[
            SourceRef(
                kind=SourceKind.URL,
                title=recipe.site_name,
                author=recipe.author,
                url=url,  # pydantic validates it as HttpUrl
                confidence=1.0,
            )
        ],
    )
