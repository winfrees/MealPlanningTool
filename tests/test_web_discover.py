"""Discovery in the web app (M5): links, the scout, the chat path, adding, promoting."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mealplan import db
from mealplan.core.normalizer import Catalog, seed_catalog
from mealplan.web.app import create_app
from tests.planning_fixtures import populate_golden
from tests.test_scout import COPY, HOT, SOUP, FakeScout, fetcher
from tests.test_web_api import HEADERS, TODAY, make_settings


def app_client(tmp_path: Path, catalog: Catalog, scout: FakeScout | None) -> TestClient:
    settings = make_settings(tmp_path)
    app = create_app(
        settings,
        today=lambda: TODAY,
        scout=(lambda: scout) if scout else None,
        fetcher=fetcher,
    )
    with db.session_scope(db.make_engine(settings.db_url)) as s:
        seed_catalog(s, catalog)
        populate_golden(s, catalog)
    client = TestClient(app, headers=HEADERS)
    assert client.post("/api/login", json={"password": "correct horse"}).status_code == 200
    return client


@pytest.fixture
def web(tmp_path: Path, catalog: Catalog) -> Iterator[TestClient]:
    with app_client(tmp_path, catalog, FakeScout(SOUP, HOT, COPY)) as c:
        yield c


def verdicts(candidates: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(c["url"], c["verdict"]) for c in candidates]


def test_presets_and_scout_availability(web, tmp_path, catalog):
    info = web.get("/api/discover").json()
    assert [p["key"] for p in info["presets"]] == ["soups", "lunches", "weeknight"]
    assert info["scout_ready"] is True and info["job"]["state"] == "idle"
    (tmp_path / "nokey").mkdir()
    with app_client(tmp_path / "nokey", catalog, None) as c:
        assert c.get("/api/discover").json()["scout_ready"] is False
        refused = c.post("/api/discover/scout", json={"preset": "soups"})
        assert refused.status_code == 400 and "chat" in refused.json()["detail"]


def test_pasted_links_are_checked(web):
    urls = f"{SOUP['url']}\n{HOT['url']}  {COPY['url']}"
    got = web.post("/api/discover/check", json={"urls": urls}).json()
    assert verdicts(got) == [
        (SOUP["url"], "ok"),
        (HOT["url"], "blocked"),
        (COPY["url"], "duplicate"),
    ]
    assert got[0]["facts"]["role"] == "soup" and got[0]["site"] == "Good Bowls"
    assert got[1]["reasons"][0].startswith("too spicy")
    assert got[2]["similar"][0]["ref"] == "core-024"
    assert web.post("/api/discover/check", json={"urls": "  "}).status_code == 400


def test_the_scout_runs_in_the_background(web):
    started = web.post("/api/discover/scout", json={"preset": "soups"})
    assert started.status_code == 200
    for _ in range(100):
        job = web.get("/api/discover/scout").json()
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "done"
    assert verdicts(job["candidates"]) == [
        (SOUP["url"], "ok"),
        (HOT["url"], "blocked"),
        (COPY["url"], "duplicate"),
    ]
    assert job["candidates"][0]["note"] == "Freezes."
    assert web.post("/api/discover/scout", json={}).status_code == 400  # no preset or request


def test_chat_prompt_and_pasted_reply(web):
    prompt = web.post("/api/discover/chat-prompt", json={"request": "cold noodle salads"}).json()
    assert "cold noodle salads" in prompt["prompt"] and "```json" in prompt["prompt"]
    assert "spinach" in prompt["prompt"]  # the golden kitchen has spinach to use up
    reply = f"Here you go: {SOUP['url']} and {HOT['url']}"
    got = web.post("/api/discover/chat", json={"reply": reply}).json()
    assert verdicts(got) == [(SOUP["url"], "ok"), (HOT["url"], "blocked")]
    assert web.post("/api/discover/chat", json={"reply": "nothing"}).status_code == 400


def test_add_rechecks_and_lands_in_review(web):
    added = web.post("/api/discover/add", json={"url": SOUP["url"]}).json()
    assert added["title"] == "Red Lentil Soup"
    review = {r["ref"]: r for r in web.get("/api/review").json()}
    assert added["ref"] in review
    # Blocked pages can't be added, whatever the client sends.
    blocked = web.post("/api/discover/add", json={"url": HOT["url"]})
    assert blocked.status_code == 400 and "spicy" in blocked.json()["detail"]
    again = web.post("/api/discover/check", json={"urls": SOUP["url"]}).json()
    assert again[0]["verdict"] == "exists"


def test_add_as_a_variant_then_approve_and_promote(web):
    added = web.post(
        "/api/discover/add", json={"url": COPY["url"], "variant_of": "core-024"}
    ).json()
    assert added["family"] == "shakshuka"
    ref = added["ref"]
    assert web.post(f"/api/review/{ref}/approve").status_code == 200

    def discovered() -> list[str]:
        found = web.get("/api/recipes", params={"collection": "discovered"}).json()
        return [r["ref"] for r in found]

    assert ref in discovered()  # alongside the golden library's own discovered recipes
    assert web.post(f"/api/recipes/{ref}/promote").json()["collection"] == "core"
    assert ref not in discovered()


def test_cli_discovery(tmp_path, monkeypatch, catalog):
    """UI-7: the same discovery from the terminal (links, chat prompt, chat reply, promote)."""
    from typer.testing import CliRunner

    from mealplan.cli import app
    from mealplan.core import discovery

    monkeypatch.setattr(discovery, "fetch_page", fetcher)
    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MEALPLAN_ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()
    assert runner.invoke(app, ["catalog", "seed"]).exit_code == 0

    dry = runner.invoke(app, ["import", "url", SOUP["url"], HOT["url"], "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "ok" in dry.output and "blocked" in dry.output and "Added" not in dry.output
    done = runner.invoke(app, ["import", "url", SOUP["url"], HOT["url"]])
    assert done.output.count("Added") == 1

    prompt = runner.invoke(app, ["discover", "prompt", "--preset", "lunches"])
    assert prompt.exit_code == 0 and "Grain salads" in prompt.output
    assert runner.invoke(app, ["discover", "prompt", "--preset", "pies"]).exit_code == 1

    reply = tmp_path / "reply.txt"
    reply.write_text(f"Here: {SOUP['url']}")
    checked = runner.invoke(app, ["discover", "chat", str(reply)])
    assert checked.exit_code == 0 and "exists" in checked.output

    no_key = runner.invoke(app, ["discover", "run", "--preset", "soups"])
    assert no_key.exit_code == 1 and "discover prompt" in no_key.output

    ref = done.output.split("Added ", 1)[1].split()[0]
    assert runner.invoke(app, ["review", "approve", ref]).exit_code == 0
    promoted = runner.invoke(app, ["recipes", "promote", ref])
    assert promoted.exit_code == 0 and "now one of your recipes" in promoted.output
