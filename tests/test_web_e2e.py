"""Browser test of the web app (M4 acceptance): plan, swap, list, inventory, review, recipes.

Runs the real server on a free port with the golden library and drives Chromium with
Playwright. Marked `e2e`; skipped when no browser is installed.
"""

import socket
import threading
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import uvicorn
from pydantic import SecretStr

from mealplan import db
from mealplan.config import Settings
from mealplan.core import library
from mealplan.core.normalizer import Catalog, seed_catalog
from mealplan.models.enums import SourceKind
from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef
from mealplan.web.app import create_app
from tests.planning_fixtures import populate_golden

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.e2e

PASSWORD = "correct horse"
PHONE = {"width": 390, "height": 844}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory, catalog: Catalog) -> Iterator[str]:
    tmp: Path = tmp_path_factory.mktemp("web")
    settings = Settings(db_path=tmp / "e2e.db", web_password=SecretStr(PASSWORD))
    app = create_app(settings, today=lambda: date(2026, 10, 3))
    with db.session_scope(db.make_engine(settings.db_url)) as s:
        seed_catalog(s, catalog)
        populate_golden(s, catalog)
        drafts = {
            # A second copy of core-024, as a re-imported PDF page would produce.
            "Easy Shakshuka": [
                "2 tablespoons olive oil",
                "1 large yellow onion, chopped",
                "2 green peppers, chopped",
                "1 (28-ounce) can crushed tomatoes",
                "6 large eggs",
                "2 cups baby spinach",
            ],
            "Pumpkin Soup": ["1 (15-ounce) can pumpkin puree", "1 onion, diced"],
        }
        for title, lines in drafts.items():
            library.create_draft(
                s,
                RecipeDraft(
                    title=title,
                    ingredients=[IngredientLine(raw_text=line) for line in lines],
                    sources=[SourceRef(kind=SourceKind.MANUAL)],
                ),
                catalog,
            )
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover - depends on the machine
            pytest.skip(f"no browser: {e}")
        yield b
        b.close()


def no_horizontal_scroll(page: object) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")  # type: ignore[attr-defined]


def test_household_workflow(base_url: str, browser: object) -> None:
    expect = playwright_api.expect
    page = browser.new_page(viewport=PHONE)  # type: ignore[attr-defined]
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    # Login gate (UI-6)
    page.goto(base_url)
    page.get_by_label("Household password").fill("wrong")
    page.get_by_role("button", name="Log in").click()
    expect(page.get_by_role("alert")).to_contain_text("wrong password")
    page.get_by_label("Household password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()

    # Week plan: plan, base week, swap, lock
    page.get_by_role("button", name="Plan this week").click()
    monday = page.locator("section.day", has_text="Mon 05 Oct")
    expect(monday).to_contain_text("Salmon, Jasmine Rice and Broccoli")
    expect(page.locator("section.day", has_text="Fri 09 Oct")).to_contain_text("Pizza Night")
    thursday = page.locator("section.day", has_text="Thu 08 Oct")
    thursday.locator(".meal", has_text="Dinner").get_by_role("button", name="Swap").click()
    thursday.get_by_label("Search recipes to swap in").fill("black bean")
    thursday.get_by_role("button", name="The Best Black Bean Burgers").click()
    expect(thursday).to_contain_text("The Best Black Bean Burgers")
    expect(thursday).to_contain_text("swapped")
    expect(page.get_by_text("Prep day · about")).to_be_visible()
    no_horizontal_scroll(page)
    page.get_by_role("button", name="Lock").click()
    expect(page.get_by_role("button", name="Re-plan")).to_be_disabled()
    expect(page.get_by_role("button", name="Unlock")).to_be_visible()

    # Shopping list: tick an item, and the tick survives a reload
    page.get_by_role("link", name="Shop").click()
    expect(page.get_by_role("heading", name="Produce")).to_be_visible()
    counter = page.locator(".toolbar .muted")
    before = counter.inner_text()
    page.get_by_label("avocado: 3").check()
    expect(counter).not_to_have_text(before)
    page.reload()
    expect(page.get_by_label("avocado: 3")).to_be_checked()
    no_horizontal_scroll(page)

    # Kitchen: add ground beef, then it moves to "Already have" on the list
    page.get_by_role("link", name="Kitchen").click()
    page.get_by_label("Ingredient", exact=True).fill("ground beef")
    page.get_by_label("Quantity", exact=True).fill("3")
    page.get_by_label("Unit", exact=True).select_option("lb")
    page.get_by_label("Location", exact=True).select_option("freezer")
    page.get_by_role("button", name="Add").click()
    expect(page.get_by_role("status")).to_have_text("Added ground beef.")
    expect(page.locator("section", has_text="Freezer")).to_contain_text("ground beef")
    no_horizontal_scroll(page)
    page.get_by_role("link", name="Shop").click()
    expect(page.locator("section", has_text="Already have")).to_contain_text("ground beef")

    # Review queue: merge a copy, approve a new recipe
    page.get_by_role("link", name="Review").click()
    shakshuka = page.locator("section.card", has_text="Easy Shakshuka")
    shakshuka.get_by_role("button", name="Same recipe: merge").first.click()
    expect(page.get_by_role("status")).to_contain_text("merged into core-024")
    page.locator("section.card", has_text="Pumpkin Soup").get_by_role(
        "button", name="Approve"
    ).click()
    expect(page.get_by_text("Nothing waiting for review.")).to_be_visible()

    # Recipes: search, open, scale, rate
    page.get_by_role("link", name="Recipes").click()
    page.get_by_label("Search").fill("tacos")
    page.get_by_role("link", name="Tacos (Meat)").click()
    expect(page.get_by_role("heading", name="Tacos (Meat)")).to_be_visible()
    page.get_by_role("button", name="More servings").click()
    expect(page.get_by_text("5 servings")).to_be_visible()
    expect(page.get_by_text("15 tortilla")).to_be_visible()  # 12 x 5/4
    page.get_by_role("button", name="5 stars").click()
    expect(page.get_by_role("status")).to_have_text("Rated 5/5.")
    no_horizontal_scroll(page)

    # Log out: the API is closed again
    page.get_by_role("button", name="Log out").click()
    expect(page.get_by_label("Household password")).to_be_visible()
    # The one expected failure: the deliberate wrong-password login above.
    unexpected = [e for e in errors if "status of 401" not in e]
    assert unexpected == [], unexpected
    assert len(errors) - len(unexpected) == 1
