"""ING-1: import the core collection from the source PDF, driven by the recipe manifest.

Two passes (requirements §7): text web prints go through the deterministic parser; every
other page type (or a web print the parser cannot read) goes to the extraction agent. All
results land as drafts in the review queue with the manifest's id, role, family and notes.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pymupdf
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mealplan.agents.extractor import (
    ExtractedRecipe,
    ExtractionRequest,
    ExtractionResult,
    Failure,
    PageContent,
    RecipeExtractor,
)
from mealplan.core import library
from mealplan.core.normalizer import Catalog
from mealplan.ingest.web_print import parse_web_print
from mealplan.models.enums import Collection, SourceKind
from mealplan.models.manifest import Manifest, ManifestRecipe
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef, StepDraft
from mealplan.models.tables import AgentCall, IngestFailure, Recipe

AGENT_NAME = "pdf-extractor"
# Page formats (manifest `format`) whose image the agent needs to see.
IMAGE_FORMATS = {
    "scanned image",
    "notebook screenshot",
    "text, title in image",
    "designed booklet",
}
MAX_IMAGE_SIDE_PX = 7000
IMAGE_DPI = 110
# Handwritten notes date from the collection's merge (Recipes_12Sept26.pdf).
SEED_RATING_DATE = date(2026, 9, 12)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already in the library
    needs_agent: list[str] = field(default_factory=list)  # no extractor configured
    failed: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


def load_pages(pdf_path: Path, numbers: list[int], with_images: bool) -> list[PageContent]:
    """Text layer per page, plus a PNG when asked for or when there is no text layer."""
    pages = []
    with pymupdf.open(pdf_path) as doc:
        for n in numbers:
            page = doc[n - 1]
            text = page.get_text("text")
            png = None
            if with_images or len(text.strip()) < 50:
                longest = max(page.rect.width, page.rect.height)
                zoom = min(IMAGE_DPI / 72, MAX_IMAGE_SIDE_PX / longest)
                png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
            pages.append(PageContent(number=n, text=text, image_png=png))
    return pages


def spent_since(session: Session, since: datetime) -> float:
    total = session.scalar(
        select(func.coalesce(func.sum(AgentCall.cost_usd), 0.0)).where(
            AgentCall.created_at >= since
        )
    )
    return float(total or 0.0)


def _log_calls(session: Session, result: ExtractionResult) -> float:
    for call in result.calls:
        session.add(
            AgentCall(
                agent=AGENT_NAME,
                model=call.model,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                latency_ms=call.latency_ms,
                outcome=call.outcome,
                cost_usd=call.cost_usd,
            )
        )
    return sum(c.cost_usd for c in result.calls)


def _best_match(recipes: list[ExtractedRecipe], title: str) -> ExtractedRecipe:
    want = library.title_tokens(title)
    return max(recipes, key=lambda r: len(want & library.title_tokens(r.title)))


def to_draft(
    extracted: ExtractedRecipe, entry: ManifestRecipe, pdf_name: str, confidence: float
) -> RecipeDraft:
    signals = library.household_signals(entry.household_notes)
    tags = set(signals.tags)
    if confidence < 0.8:
        tags.add("vision-extracted")
    return RecipeDraft(
        title=entry.title,  # the manifest's curated title
        servings=extracted.servings if extracted.servings and extracted.servings > 0 else None,
        prep_minutes=extracted.prep_minutes,
        cook_minutes=extracted.cook_minutes,
        total_minutes=extracted.total_minutes,
        meal_role=entry.meal_role,
        tags=sorted(tags),
        collection=Collection.CORE,
        ingredients=[IngredientLine(raw_text=line) for line in extracted.ingredients if line],
        steps=[
            StepDraft(
                text=s.text,
                equipment=s.equipment,
                active_minutes=max(s.active_minutes or 0, 0),
                passive_minutes=max(s.passive_minutes or 0, 0),
            )
            for s in extracted.steps
            if s.text.strip()
        ],
        sources=[
            SourceRef(
                kind=SourceKind.PDF,
                title=entry.source,
                url=entry.url or None,
                file=pdf_name,
                pages=entry.page_list,
                confidence=confidence,
            )
        ],
    )


def import_manifest(
    session: Session,
    pdf_path: Path,
    manifest: Manifest,
    catalog: Catalog,
    extractor: RecipeExtractor | None,
    only: set[str] | None = None,
    weekly_budget_usd: float = 10.0,
    now: datetime | None = None,
) -> ImportReport:
    report = ImportReport()
    now = now or datetime.now(UTC).replace(tzinfo=None)
    existing = set(session.scalars(select(Recipe.ref)))

    for entry in manifest.recipes:
        if only is not None and entry.id not in only:
            continue
        if entry.id in existing:
            report.skipped.append(entry.id)
            continue

        pages = load_pages(pdf_path, entry.page_list, entry.format in IMAGE_FORMATS)
        extracted = None
        confidence = 1.0
        if entry.format == "text":
            extracted = parse_web_print(
                "\n".join(p.text for p in pages), entry.title, entry.page_list
            )

        if extracted is None:
            if extractor is None:
                report.needs_agent.append(entry.id)
                continue
            if spent_since(session, now - timedelta(days=7)) >= weekly_budget_usd:
                raise BudgetExceeded(
                    f"weekly agent budget of ${weekly_budget_usd:.2f} reached; "
                    f"stopped before {entry.id}"
                )
            result = extractor.extract(ExtractionRequest(pages, target_title=entry.title))
            report.cost_usd += _log_calls(session, result)
            failure = result.failure
            if failure is None and not result.recipes:
                failure = Failure("validation", "no recipe found on the pages")
            if failure is not None:
                session.add(
                    IngestFailure(
                        ref=entry.id,
                        source_file=pdf_path.name,
                        pages=entry.pages,
                        stage=failure.stage,
                        error=failure.error,
                        raw_output=failure.raw_output,
                    )
                )
                report.failed.append(entry.id)
                continue
            extracted = _best_match(result.recipes, entry.title)
            vision_only = all(len(p.text.strip()) < 50 for p in pages)
            confidence = 0.7 if vision_only else 0.9

        create_from_manifest(
            session, to_draft(extracted, entry, pdf_path.name, confidence), entry, catalog
        )
        report.created.append(entry.id)
    return report


def create_from_manifest(
    session: Session, draft: RecipeDraft, entry: ManifestRecipe, catalog: Catalog
) -> Recipe:
    """A draft under the manifest's id, with its household notes, seed rating and family."""
    recipe = library.create_draft(session, draft, catalog, ref=entry.id)
    recipe.household_notes = entry.household_notes
    signals = library.household_signals(entry.household_notes)
    if signals.rating is not None:
        library.rate(
            session,
            recipe,
            signals.rating,
            SEED_RATING_DATE,
            notes=f"seed from note: {entry.household_notes}",
        )
    if entry.variant_family:
        library.add_to_family(session, recipe, entry.variant_family)
    session.flush()
    return recipe
