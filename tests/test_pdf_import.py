"""ING-1 manifest-driven PDF import, the deterministic web-print path, and guardrails."""

from datetime import datetime, timedelta
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from mealplan.agents.extractor import (
    CallRecord,
    ExtractedRecipe,
    ExtractedStep,
    ExtractionRequest,
    ExtractionResult,
    Failure,
)
from mealplan.core import library
from mealplan.ingest import review_queue
from mealplan.ingest.pdf import BudgetExceeded, import_manifest, load_pages
from mealplan.ingest.web_print import parse_duration, parse_web_print
from mealplan.models.enums import RecipeStatus
from mealplan.models.manifest import Manifest
from mealplan.models.tables import AgentCall, IngestFailure, Rating, RecipeFamily

WEB_PRINT = """Easy Shakshuka
Prep Time 10 mins  Cook Time 25 mins  Total Time 35 mins
Servings 6
Ingredients
US Customary
▢ Extra virgin olive oil
▢ 1 large yellow onion, chopped
▢ 2 green peppers, cored and
chopped
For the topping:
▢ 6 large eggs
▢ Salt and pepper
Instructions
1. Heat the oil in a large skillet.
2. Add the onion and peppers and cook until soft,
about 5 minutes.
3. Crack in the eggs and cover.
Notes
Make the sauce ahead.
Nutrition
Calories: 200"""

TALL_CAPTURE = """Kitchen Frau
Misir Wat (Ethiopian Red Lentil Stew)
1 cup red lentils
2 tablespoons niter kibbeh
Great recipe! Reply
Advertisement"""


def test_parse_web_print():
    r = parse_web_print(WEB_PRINT, "Easy Shakshuka", [39, 40])
    assert r is not None
    assert (r.servings, r.prep_minutes, r.cook_minutes, r.total_minutes) == (6, 10, 25, 35)
    assert r.ingredients == [
        "Extra virgin olive oil",
        "1 large yellow onion, chopped",
        "2 green peppers, cored and chopped",
        "6 large eggs",
        "Salt and pepper",
    ]
    assert [s.text for s in r.steps] == [
        "Heat the oil in a large skillet.",
        "Add the onion and peppers and cook until soft, about 5 minutes.",
        "Crack in the eggs and cover.",
    ]
    assert r.pages == [39, 40]


def test_parse_web_print_needs_both_headings():
    assert parse_web_print(TALL_CAPTURE, "Misir Wat", [41]) is None
    assert parse_web_print("Ingredients\n1 egg\n2 eggs", "X", [1]) is None


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("1 hr 20 mins", 80), ("45 minutes", 45), ("2 hours", 120), ("soon", None)],
)
def test_parse_duration(text, minutes):
    assert parse_duration(text) == minutes


def make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for text in (WEB_PRINT, TALL_CAPTURE, ""):
        page = doc.new_page(height=1400)
        if text:
            page.insert_text((40, 40), text, fontsize=9)
    doc.save(path)
    doc.close()
    return path


MANIFEST = Manifest.model_validate(
    {
        "source_pdf": "Recipes_12Sept26.pdf",
        "pages": 3,
        "recipes": [
            {
                "id": "core-024",
                "title": "Easy Shakshuka",
                "source": "The Mediterranean Dish",
                "url": "https://www.themediterraneandish.com/shakshuka-recipe/",
                "pages": "1",
                "format": "text",
                "meal_role": "dinner",
                "variant_family": "shakshuka",
                "household_notes": "Make-ahead sauce tip",
            },
            {
                "id": "core-026",
                "title": "Misir Wat (Lentil Stew)",
                "source": "Kitchen Frau",
                "pages": "2",
                "format": "tall web capture",
                "meal_role": "dinner",
            },
            {
                "id": "core-049",
                "title": "Grilled Bruschetta Chicken",
                "source": "Once A Month Meals",
                "pages": "3",
                "format": "notebook screenshot",
                "meal_role": "dinner",
                "household_notes": "Handwritten: Excellent x2 (freezer)",
            },
        ],
    }
)


def extracted(title: str, lines: list[str], page: int) -> ExtractedRecipe:
    step = ExtractedStep(text="Cook.", equipment=[], active_minutes=None, passive_minutes=None)
    return ExtractedRecipe(
        title=title,
        servings=4,
        prep_minutes=None,
        cook_minutes=None,
        total_minutes=None,
        ingredients=lines,
        steps=[step],
        pages=[page],
    )


CALL = CallRecord("claude-opus-5", 1000, 200, 900, "ok", 0.01)


class FakeExtractor:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.requests: list[ExtractionRequest] = []
        self.fail = fail or set()

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.requests.append(request)
        title = request.target_title or ""
        if title in self.fail:
            return ExtractionResult(
                calls=[CALL, CALL], failure=Failure("grounding", "not in text layer", "{}")
            )
        page = request.pages[0].number
        lines = ["1 cup red lentils"] if "Misir" in title else ["2 lb chicken breasts"]
        return ExtractionResult(recipes=[extracted(title, lines, page)], calls=[CALL])


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "Recipes_12Sept26.pdf")


def test_load_pages_renders_images_for_image_pages(pdf):
    text_page, capture, blank = load_pages(pdf, [1, 2, 3], with_images=False)
    assert "Ingredients" in text_page.text and text_page.image_png is None
    assert capture.image_png is None
    assert blank.image_png is not None and blank.image_png.startswith(b"\x89PNG")


def test_import_two_passes(session, catalog, pdf):
    fake = FakeExtractor()
    report = import_manifest(session, pdf, MANIFEST, catalog, fake)
    assert report.created == ["core-024", "core-026", "core-049"]
    assert report.cost_usd == pytest.approx(0.02)

    # Web print: deterministic, no agent call.
    assert [r.target_title for r in fake.requests] == [
        "Misir Wat (Lentil Stew)",
        "Grilled Bruschetta Chicken",
    ]
    shak = library.get_recipe(session, "core-024")
    assert shak.status is RecipeStatus.DRAFT
    assert shak.servings == 6
    assert shak.sources[0].confidence == 1.0
    assert shak.sources[0].url.startswith("https://www.themediterraneandish.com")
    assert "make-ahead" in shak.tags
    assert shak.family is not None and shak.family.name == "shakshuka"

    # Screenshot page: vision-only, tagged, notes become a seed rating and tags.
    chicken = library.get_recipe(session, "core-049")
    assert chicken.sources[0].confidence == 0.7
    assert {"vision-extracted", "freezer-friendly", "double-batch"} <= set(chicken.tags)
    ratings = session.scalars(select(Rating).where(Rating.recipe_id == chicken.id)).all()
    assert [r.score for r in ratings] == [5]
    assert chicken.household_notes == "Handwritten: Excellent x2 (freezer)"

    assert library.get_recipe(session, "core-026").sources[0].confidence == 0.9
    assert len(session.scalars(select(AgentCall)).all()) == 2

    again = import_manifest(session, pdf, MANIFEST, catalog, fake)
    assert again.skipped == ["core-024", "core-026", "core-049"]
    assert again.created == []


def test_without_an_agent_only_web_prints_import(session, catalog, pdf):
    report = import_manifest(session, pdf, MANIFEST, catalog, extractor=None)
    assert report.created == ["core-024"]
    assert report.needs_agent == ["core-026", "core-049"]


def test_failures_go_to_the_failed_queue(session, catalog, pdf):
    fake = FakeExtractor(fail={"Misir Wat (Lentil Stew)"})
    report = import_manifest(session, pdf, MANIFEST, catalog, fake, only={"core-026"})
    assert report.failed == ["core-026"]
    failure = session.scalars(select(IngestFailure)).one()
    assert (failure.ref, failure.stage, failure.pages) == ("core-026", "grounding", "2")
    outcomes = session.scalars(select(AgentCall.outcome)).all()
    assert len(outcomes) == 2


def test_weekly_budget_stops_the_import(session, catalog, pdf):
    now = datetime(2026, 9, 26, 12, 0)
    session.add(
        AgentCall(
            agent="pdf-extractor",
            model="claude-opus-5",
            outcome="ok",
            cost_usd=9.995,
            created_at=now - timedelta(days=1),
        )
    )
    session.flush()
    fake = FakeExtractor()
    with pytest.raises(BudgetExceeded, match="core-049"):
        import_manifest(session, pdf, MANIFEST, catalog, fake, weekly_budget_usd=10.0, now=now)
    # core-026 ran and pushed spend over the cap; web print needs no budget.
    assert [r.ref for r in review_queue.pending(session)] == ["core-024", "core-026"]
    families = session.scalars(select(RecipeFamily.name)).all()
    assert families == ["shakshuka"]


def test_old_spend_does_not_count(session, catalog, pdf):
    now = datetime(2026, 9, 26, 12, 0)
    session.add(
        AgentCall(
            agent="pdf-extractor",
            model="claude-opus-5",
            outcome="ok",
            cost_usd=50.0,
            created_at=now - timedelta(days=8),
        )
    )
    report = import_manifest(session, pdf, MANIFEST, catalog, FakeExtractor(), now=now)
    assert len(report.created) == 3
