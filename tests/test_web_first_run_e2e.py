"""Browser test of a new install (UI-8): log in, import the PDF, approve, plan the week."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import SecretStr

from mealplan.config import Settings
from mealplan.web.app import create_app
from tests.test_first_run import data_dir  # noqa: F401 (fixture)
from tests.test_pdf_import import FakeExtractor, make_pdf
from tests.test_web_e2e import PASSWORD, PHONE, browser, no_horizontal_scroll, serve_app

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.e2e
__all__ = ["browser"]


def test_new_household_gets_to_a_planned_week(
    tmp_path: Path,
    data_dir: Path,  # noqa: F811
    browser: object,
) -> None:
    expect = playwright_api.expect
    settings = Settings(
        db_path=tmp_path / "new.db", data_dir=data_dir, web_password=SecretStr(PASSWORD)
    )
    fake = FakeExtractor()
    app = create_app(
        settings,
        today=lambda: date(2026, 10, 3),
        extractor=lambda: fake,
        key_checker=lambda key: None,
        env_file=tmp_path / ".env",
    )
    pdf = make_pdf(tmp_path / "Recipes_12Sept26.pdf")

    with serve_app(app) as url:
        page = browser.new_page(viewport=PHONE)  # type: ignore[attr-defined]
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.get_by_label("Household password").fill(PASSWORD)
        page.get_by_role("button", name="Log in").click()

        start = page.locator("section.start")
        expect(start.get_by_role("heading", name="Get started")).to_be_visible()
        expect(start.get_by_role("button", name="Plan this week")).to_be_disabled()
        no_horizontal_scroll(page)

        start.get_by_label("Recipe PDF").set_input_files(pdf)
        expect(start).to_contain_text("Imported 3 recipes", timeout=15000)
        expect(start).to_contain_text("3 recipes waiting for a check")

        # The Claude key box: a model name is refused with a hint; a key is checked and saved.
        key_box = start.get_by_label("Anthropic API key")
        key_box.fill("claude-opus-5")
        start.get_by_role("button", name="Save key").click()
        expect(start.get_by_role("alert")).to_contain_text("sk-ant-")
        key_box.fill("sk-ant-api03-" + "x" * 40)
        start.get_by_role("button", name="Save key").click()
        expect(start).to_contain_text("Key checked and saved")
        expect(start).to_contain_text("ends in xxxx")
        assert "MEALPLAN_ANTHROPIC_API_KEY=sk-ant-" in (tmp_path / ".env").read_text()

        start.get_by_role("button", name="Approve the ones that look fine").click()
        # With recipes approved, the page moves on to planning.
        expect(page.get_by_text("No plan for this week yet.")).to_be_visible()
        expect(page.get_by_role("link", name="1 imported recipe")).to_be_visible()
        page.get_by_role("button", name="Plan this week").click()
        expect(page.locator("section.day", has_text="Mon 05 Oct")).to_contain_text(
            "Salmon, Jasmine Rice and Broccoli"
        )
        assert [e for e in errors if "400" not in e] == []  # the refused key logs one 400
