"""Import recipes read by Claude in a chat (claude.ai), no API key needed (ING-1, ING-3, UI-8).

The recipes still missing from the library are split into small batches. For each batch the
app gives a short PDF of just those pages and a prompt; the person attaches both in a Claude
chat and pastes the reply back. The reply is JSON in the same shape as the API extractor's
output plus the manifest id, so it goes through the same draft path (`to_draft`) and lands
in the review queue like any other import. Pasted text is data: it is parsed and validated,
never followed.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.agents.extractor import ExtractedRecipe, ExtractedStep
from mealplan.core import library
from mealplan.core.normalizer import Catalog
from mealplan.ingest.pdf import create_from_manifest, to_draft
from mealplan.models.manifest import Manifest, ManifestRecipe
from mealplan.models.tables import Recipe

MAX_BATCH_PAGES = 12
MAX_BATCH_RECIPES = 8
CHAT_CONFIDENCE = 0.75  # read by Claude from page images, checked by a person in review
CHAT_TAG = "claude-chat"
MAX_REPLY_CHARS = 2_000_000


class ChatImportError(ValueError):
    pass


# --- batches and prompts ----------------------------------------------------------------------


@dataclass(frozen=True)
class Batch:
    number: int
    entries: tuple[ManifestRecipe, ...]

    @property
    def pages(self) -> list[int]:
        return [p for e in self.entries for p in e.page_list]

    def file_name(self) -> str:
        return f"recipes-batch-{self.number:02d}.pdf"


def missing(session: Session, manifest: Manifest) -> list[ManifestRecipe]:
    """Manifest recipes not yet in the library (as a draft or approved)."""
    have = set(session.scalars(select(Recipe.ref)))
    return [e for e in manifest.recipes if e.id not in have]


def pending_batches(session: Session, manifest: Manifest) -> list[Batch]:
    """Stable batches over the whole manifest, each cut down to its recipes still missing.
    Numbers don't shift as batches are imported; finished batches drop out."""
    todo = {e.id for e in missing(session, manifest)}
    out = []
    for b in batches(manifest.recipes):
        left = tuple(e for e in b.entries if e.id in todo)
        if left:
            out.append(Batch(b.number, left))
    return out


def batches(entries: list[ManifestRecipe]) -> list[Batch]:
    """Consecutive groups small enough for one chat: few pages, few recipes."""
    groups: list[list[ManifestRecipe]] = []
    for entry in entries:
        current = groups[-1] if groups else None
        pages = sum(len(e.page_list) for e in current) if current else 0
        if (
            current is None
            or len(current) >= MAX_BATCH_RECIPES
            or pages + len(entry.page_list) > MAX_BATCH_PAGES
        ):
            groups.append([entry])
        else:
            current.append(entry)
    return [Batch(i, tuple(g)) for i, g in enumerate(groups, 1)]


def batch_pdf(source_pdf: Path, batch: Batch) -> bytes:
    """Just the batch's pages, in order, as a small PDF to attach to the chat."""
    with pymupdf.open(source_pdf) as src, pymupdf.open() as out:
        for page in batch.pages:
            out.insert_pdf(src, from_page=page - 1, to_page=page - 1)
        data: bytes = out.tobytes(garbage=3, deflate=True)
    return data


def _page_span(start: int, count: int) -> str:
    return f"page {start}" if count == 1 else f"pages {start}-{start + count - 1}"


EXAMPLE = {
    "recipes": [
        {
            "id": "core-000",
            "title": "Recipe title as printed",
            "servings": 4,
            "prep_minutes": 15,
            "cook_minutes": 30,
            "total_minutes": None,
            "ingredients": ["2 tablespoons olive oil", "1 large onion, chopped"],
            "steps": [
                {
                    "text": "Soften the onion in the oil.",
                    "equipment": ["stove"],
                    "active_minutes": 10,
                    "passive_minutes": 0,
                }
            ],
        }
    ]
}


def prompt(batch: Batch) -> str:
    """What to paste into the chat alongside the batch PDF."""
    lines = []
    page = 1
    for e in batch.entries:
        count = len(e.page_list)
        lines.append(f'- {e.id}: "{e.title}" ({_page_span(page, count)})')
        page += count
    listing = "\n".join(lines)
    example = json.dumps(EXAMPLE, indent=2)
    return f"""\
I've attached {batch.file_name()}, {len(batch.pages)} pages from my family's recipe \
collection. Please read each recipe below and write it out as JSON so I can import it into \
my meal planner.

Recipes in the file (page numbers are pages of the attached file):
{listing}

How to write each recipe:
- id: the id from the list above (for example {batch.entries[0].id}). One entry per recipe \
in the list.
- ingredients: one line per ingredient, exactly as written (same numbers, units and notes). \
Leave out section headings like "For the sauce:". Include handwritten additions.
- steps: in order, as written. Set equipment (oven, stove, slow cooker, pressure cooker, \
grill, microwave, mixer) and active_minutes/passive_minutes only when the page states them or \
they are plain from the step; otherwise use [] and null.
- servings and times: only as stated on the page; otherwise null.
- Ignore ads, comments, ratings and nutrition panels.
- If a page is unreadable, still include the recipe with what you can read.

Reply with only one JSON code block, in exactly this shape:

```json
{example}
```"""


# --- reading the reply ------------------------------------------------------------------------


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ChatStep(_Loose):
    text: str
    equipment: list[str] = Field(default_factory=list)
    active_minutes: int | None = Field(default=None, ge=0)
    passive_minutes: int | None = Field(default=None, ge=0)


class ChatRecipe(_Loose):
    id: str = ""
    title: str = ""
    servings: float | None = None
    prep_minutes: int | None = Field(default=None, ge=0)
    cook_minutes: int | None = Field(default=None, ge=0)
    total_minutes: int | None = Field(default=None, ge=0)
    ingredients: list[str] = Field(default_factory=list)
    steps: list[ChatStep] = Field(default_factory=list)

    @field_validator("steps", mode="before")
    @classmethod
    def _steps_may_be_strings(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [{"text": s} if isinstance(s, str) else s for s in v]
        return v

    @field_validator("servings", mode="before")
    @classmethod
    def _servings_may_be_text(cls, v: Any) -> Any:
        if isinstance(v, str):
            found = re.search(r"\d+(\.\d+)?", v)
            return float(found.group()) if found else None
        return v


def _json_text(reply: str) -> str:
    fenced: list[str] = re.findall(
        r"```(?:json)?\s*(.*?)```", reply, flags=re.DOTALL | re.IGNORECASE
    )
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", reply, flags=re.DOTALL | re.IGNORECASE)
    for block in fenced:
        if block.strip().startswith(("{", "[")):
            return block.strip()
    start = min((i for i in (reply.find("{"), reply.find("[")) if i >= 0), default=-1)
    end = max(reply.rfind("}"), reply.rfind("]"))
    if start < 0 or end <= start:
        raise ChatImportError("no JSON found; paste Claude's whole reply, including the code block")
    return reply[start : end + 1]


def parse_reply(reply: str) -> list[dict[str, Any]]:
    if len(reply) > MAX_REPLY_CHARS:
        raise ChatImportError("that reply is too long; import one batch at a time")
    try:
        data = json.loads(_json_text(reply))
    except json.JSONDecodeError as e:
        raise ChatImportError(
            f"the JSON is incomplete or broken near line {e.lineno}; if the reply was cut "
            "off, ask Claude to continue, or to redo fewer recipes"
        ) from None
    if isinstance(data, dict) and "recipes" in data:
        data = data["recipes"]
    elif isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ChatImportError('expected {"recipes": [...]} as in the prompt')
    return data


@dataclass
class PreviewItem:
    id: str
    title: str
    status: str  # new | exists | error
    ingredients: int = 0
    steps: int = 0
    problems: list[str] = field(default_factory=list)
    recipe: ChatRecipe | None = None
    entry: ManifestRecipe | None = None


def _find_entry(recipe: ChatRecipe, manifest: Manifest) -> ManifestRecipe | None:
    by_id = {e.id: e for e in manifest.recipes}
    if recipe.id.strip().lower() in by_id:
        return by_id[recipe.id.strip().lower()]
    if recipe.title:
        want = library.title_tokens(recipe.title)
        scored = [(len(want & library.title_tokens(e.title)), e) for e in manifest.recipes]
        best, entry = max(scored, key=lambda s: s[0], default=(0, None))
        if entry is not None and best >= max(2, len(want) // 2):
            return entry
    return None


def preview(session: Session, manifest: Manifest, reply: str) -> list[PreviewItem]:
    """What an import would do, recipe by recipe, without writing anything."""
    have = set(session.scalars(select(Recipe.ref)))
    items: list[PreviewItem] = []
    seen: set[str] = set()
    for i, raw in enumerate(parse_reply(reply), 1):
        label = str(raw.get("id") or raw.get("title") or f"recipe {i}")
        try:
            recipe = ChatRecipe.model_validate(raw)
        except ValidationError as e:
            problems = [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
            ]
            items.append(PreviewItem(label, str(raw.get("title", "")), "error", problems=problems))
            continue
        entry = _find_entry(recipe, manifest)
        item = PreviewItem(
            entry.id if entry else label,
            entry.title if entry else recipe.title,
            "new",
            ingredients=len([x for x in recipe.ingredients if x.strip()]),
            steps=len([s for s in recipe.steps if s.text]),
            recipe=recipe,
            entry=entry,
        )
        if entry is None:
            item.status = "error"
            item.problems.append("not in the recipe manifest; check the id")
        elif entry.id in have:
            item.status = "exists"
        elif entry.id in seen:
            item.status = "error"
            item.problems.append("appears twice in the reply")
        else:
            seen.add(entry.id)
            if not item.ingredients:
                item.problems.append("no ingredients")
            if not item.steps:
                item.problems.append("no steps")
        items.append(item)
    if not items:
        raise ChatImportError("the reply has no recipes")
    return items


def import_reply(
    session: Session, manifest: Manifest, catalog: Catalog, reply: str
) -> list[PreviewItem]:
    """Create a draft for every new recipe in the reply. Existing ones are left alone (REC-8)."""
    items = preview(session, manifest, reply)
    for item in items:
        if item.status != "new" or item.recipe is None or item.entry is None:
            continue
        r = item.recipe
        extracted = ExtractedRecipe(
            title=r.title or item.entry.title,
            servings=r.servings,
            prep_minutes=r.prep_minutes,
            cook_minutes=r.cook_minutes,
            total_minutes=r.total_minutes,
            ingredients=[x for x in r.ingredients if x.strip()],
            steps=[
                ExtractedStep(
                    text=s.text,
                    equipment=s.equipment,
                    active_minutes=s.active_minutes,
                    passive_minutes=s.passive_minutes,
                )
                for s in r.steps
                if s.text
            ],
            pages=item.entry.page_list,
        )
        draft = to_draft(extracted, item.entry, manifest.source_pdf, CHAT_CONFIDENCE)
        draft = draft.model_copy(update={"tags": sorted({*draft.tags, CHAT_TAG})})
        create_from_manifest(session, draft, item.entry, catalog)
        item.status = "created"
    return items
