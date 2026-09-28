"""Browser test of a new install (UI-8): log in, import the PDF, approve, plan the week."""

import re
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


def test_chat_import_then_edit_in_the_browser(
    tmp_path: Path,
    data_dir: Path,  # noqa: F811
    browser: object,
) -> None:
    """UI-8 without an API key: batch, prompt, paste the reply, import, then fix it (UI-5)."""
    import json

    expect = playwright_api.expect
    settings = Settings(
        db_path=tmp_path / "chat.db", data_dir=data_dir, web_password=SecretStr(PASSWORD)
    )
    app = create_app(settings, today=lambda: date(2026, 10, 3), extractor=lambda: None)
    pdf = make_pdf(tmp_path / "Recipes_12Sept26.pdf")
    claude_reply = (
        "Here are the recipes:\n```json\n"
        + json.dumps(
            {
                "recipes": [
                    {
                        "id": "core-049",
                        "title": "Grilled Bruschetta Chicken",
                        "servings": 4,
                        "ingredients": ["2 lb chicken breasts", "1 c. glorp"],
                        "steps": ["Grill the chicken."],
                    }
                ]
            }
        )
        + "\n```"
    )

    with serve_app(app) as url:
        page = browser.new_page(viewport=PHONE)  # type: ignore[attr-defined]
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.get_by_label("Household password").fill(PASSWORD)
        page.get_by_role("button", name="Log in").click()

        start = page.locator("section.start")
        start.get_by_label("Recipe PDF").set_input_files(pdf)
        expect(start).to_contain_text("Imported 1 recipe", timeout=15000)

        chat = start.locator("details.chat-details")
        expect(chat).to_have_attribute("open", "")  # opened: no key and pages need Claude
        expect(chat).to_contain_text("2 recipes still to import")
        expect(chat.get_by_role("link", name="Download PDF")).to_have_attribute(
            "href", "/api/chat-import/1/pdf"
        )
        # No clipboard access here (as on plain http across the LAN): the prompt is shown to copy.
        chat.get_by_role("button", name="Copy prompt").click()
        expect(chat.get_by_label("Prompt for batch 1")).to_have_value(re.compile("core-049"))

        chat.get_by_label("Claude's reply").fill(claude_reply)
        chat.get_by_role("button", name="Check reply").click()
        expect(chat.locator(".preview")).to_contain_text("core-049 Grilled Bruschetta Chicken")
        chat.get_by_role("button", name="Import 1 recipe").click()
        expect(chat).to_contain_text("Imported 1 recipe")
        expect(chat).to_contain_text("1 recipe still to import")

        # Fix the unmatched ingredient in the editor, from the review queue.
        page.goto(f"{url}/#/review")
        card = page.locator("section.card", has_text="Grilled Bruschetta Chicken")
        expect(card).to_contain_text("glorp")
        card.get_by_role("link", name="Edit").click()
        expect(page.get_by_role("heading", name="Edit core-049")).to_be_visible()
        expect(page.locator(".card.warn")).to_contain_text("glorp")
        page.get_by_label("Ingredients").fill("2 lb chicken breasts\n1 cup cherry tomatoes")
        page.get_by_label("Hands-on min").first.fill("20")
        page.get_by_label("grill").first.check()
        page.get_by_role("button", name="Add a step").click()
        page.get_by_role("textbox", name="Step 2").fill("Top with the tomatoes.")
        page.get_by_role("button", name="Save").click()
        expect(page.get_by_role("status")).to_contain_text("No issues left")
        no_horizontal_scroll(page)
        page.get_by_role("link", name="Done").click()
        card = page.locator("section.card", has_text="Grilled Bruschetta Chicken")
        expect(card).to_contain_text("No issues found")
        assert errors == []


def test_choosing_the_local_reader(
    tmp_path: Path,
    data_dir: Path,  # noqa: F811
    browser: object,
) -> None:
    """ING-1 locally: the Setup page checks Ollama and Docling and saves the reader choice."""
    from mealplan.agents.local_extractor import LocalStatus

    expect = playwright_api.expect
    settings = Settings(
        db_path=tmp_path / "local.db", data_dir=data_dir, web_password=SecretStr(PASSWORD)
    )
    ready = LocalStatus(True, True, True, ("qwen2.5:7b",), "Ready.")
    app = create_app(
        settings,
        today=lambda: date(2026, 10, 3),
        env_file=tmp_path / ".env",
        local_checker=lambda: ready,
    )
    with serve_app(app) as url:
        page = browser.new_page(viewport=PHONE)  # type: ignore[attr-defined]
        page.goto(f"{url}/#/start")
        page.get_by_label("Household password").fill(PASSWORD)
        page.get_by_role("button", name="Log in").click()
        reader = page.locator(".reader")
        reader.get_by_role("button", name="Check local model").click()
        expect(reader).to_contain_text("Import will use: local model (qwen2.5:7b)")
        expect(reader).to_contain_text("Ready.")
        reader.get_by_label("Reader").select_option("none")
        expect(reader).to_contain_text("Import will use: nobody")
        no_horizontal_scroll(page)
    assert "MEALPLAN_EXTRACTOR=none" in (tmp_path / ".env").read_text()
