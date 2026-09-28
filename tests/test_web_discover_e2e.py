"""Browser test of Discover (M5): scout, check, add as a variant, pasted links, chat path."""

import re
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from mealplan import db
from mealplan.config import Settings
from mealplan.core.normalizer import Catalog, seed_catalog
from mealplan.web.app import create_app
from tests.planning_fixtures import populate_golden
from tests.test_scout import COPY, HOT, SOUP, FakeScout, fetcher
from tests.test_web_e2e import PASSWORD, PHONE, browser, no_horizontal_scroll, serve_app

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.e2e
__all__ = ["browser"]


def make_app(tmp_path: Path, catalog: Catalog, scout: FakeScout | None) -> FastAPI:
    settings = Settings(db_path=tmp_path / "d.db", web_password=SecretStr(PASSWORD))
    app = create_app(
        settings,
        today=lambda: date(2026, 10, 3),
        scout=(lambda: scout) if scout else None,
        fetcher=fetcher,
    )
    with db.session_scope(db.make_engine(settings.db_url)) as s:
        seed_catalog(s, catalog)
        populate_golden(s, catalog)
    return app


def log_in(page: object, url: str) -> None:
    page.goto(f"{url}/#/discover")  # type: ignore[attr-defined]
    page.get_by_label("Household password").fill(PASSWORD)  # type: ignore[attr-defined]
    page.get_by_role("button", name="Log in").click()  # type: ignore[attr-defined]


def test_scout_finds_checks_and_adds(tmp_path: Path, catalog: Catalog, browser: object) -> None:
    expect = playwright_api.expect
    with serve_app(make_app(tmp_path, catalog, FakeScout(SOUP, HOT, COPY))) as url:
        page = browser.new_page(viewport=PHONE)  # type: ignore[attr-defined]
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        log_in(page, url)

        page.get_by_role("button", name="Soups for Sunday prep").click()
        soup = page.locator("li.candidate", has_text="Red Lentil Soup")
        expect(soup).to_contain_text("fits", timeout=15000)
        expect(soup).to_contain_text("Suggested because: Freezes.")
        expect(soup).to_contain_text("soup")
        shak = page.locator("li.candidate", has_text="Easy Shakshuka")
        expect(shak).to_contain_text("looks familiar")
        # The spicy one is left out, collapsed, with the reason.
        page.get_by_text("1 link left out").click()
        expect(page.locator("li.candidate", has_text="Fiery Wings")).to_contain_text("too spicy")
        no_horizontal_scroll(page)

        soup.get_by_role("button", name="Add to Review").click()
        expect(soup.get_by_role("status")).to_contain_text("Added as disc-")
        shak.get_by_role("button", name="Add as a version of Easy Shakshuka").click()
        expect(shak.get_by_role("status")).to_contain_text("in the shakshuka family")

        # Both wait in Review as drafts; the recipe page offers promotion once approved.
        page.goto(f"{url}/#/review")
        card = page.locator("section.card", has_text="Red Lentil Soup")
        card.get_by_role("button", name="Approve").click()
        page.goto(f"{url}/#/recipes")
        page.get_by_label("Collection").select_option("discovered")
        page.get_by_role("link", name="Red Lentil Soup").click()
        page.get_by_role("button", name="move it to your recipes now").click()
        expect(page.get_by_role("status")).to_contain_text("Moved to your recipes")
        assert errors == []


def test_links_and_chat_path_without_a_key(
    tmp_path: Path, catalog: Catalog, browser: object
) -> None:
    expect = playwright_api.expect
    with serve_app(make_app(tmp_path, catalog, None)) as url:
        page = browser.new_page(viewport=PHONE)  # type: ignore[attr-defined]
        log_in(page, url)

        page.get_by_label("Recipe links").fill(SOUP["url"])
        page.get_by_role("button", name="Check links").click()
        expect(page.locator("li.candidate", has_text="Red Lentil Soup")).to_contain_text("fits")

        page.get_by_label("What are you looking for").fill("cold noodle salads")
        page.get_by_role("button", name="Make prompt").click()
        expect(page.get_by_label("Chat prompt")).to_have_value(re.compile("cold noodle salads"))
        page.get_by_label("Claude's reply").fill(f"Try {HOT['url']} and {COPY['url']}")
        page.get_by_role("button", name="Check the links").click()
        expect(page.locator("li.candidate", has_text="Easy Shakshuka")).to_contain_text(
            "looks familiar"
        )
        no_horizontal_scroll(page)
