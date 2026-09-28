"""The web recipe scout (M5, §5): the agent proposes links, the core checks every one.

A real Anthropic client on a mock HTTP transport replays scripted Messages API responses, so
the request shape, the tool loop and the guardrails are tested with no network or API cost.
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import anthropic
import httpx2
import pytest
from sqlalchemy import select

from mealplan.agents.extractor import AccountError
from mealplan.agents.scout import (
    BLOCKED_DOMAINS,
    ClaudeScout,
    ScoutResult,
    ScoutSuggestion,
    chat_prompt,
    parse_link_reply,
    presets,
    request_text,
)
from mealplan.core.discovery import DiscoveryError, library_search, scout_candidates
from mealplan.core.preferences import HouseholdPrefs
from mealplan.ingest.fetch import Page
from mealplan.ingest.pdf import BudgetExceeded
from mealplan.models.tables import AgentCall
from tests.planning_fixtures import populate_golden
from tests.test_discovery import page, recipe_page

KEY = "sk-ant-api03-" + "x" * 40


def message(content: list[dict[str, Any]], stop: str, searches: int = 0) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "server_tool_use": {"web_search_requests": searches},
        },
    }


def answer(*suggestions: dict[str, str]) -> dict[str, Any]:
    text = json.dumps({"suggestions": list(suggestions)})
    return message([{"type": "text", "text": text}], "end_turn", searches=3)


def ask_library(query: str) -> dict[str, Any]:
    return message(
        [
            {"type": "text", "text": "Checking the library first."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "search_library",
                "input": {"query": query},
            },
        ],
        "tool_use",
    )


class Anthropic:
    """Scripted responses (a dict body or an HTTP status); records each request body."""

    def __init__(self, *replies: dict[str, Any] | int) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        reply = self.replies.pop(0)
        if isinstance(reply, int):
            text = "Your credit balance is too low" if reply == 400 else "nope"
            body = {"type": "error", "error": {"type": "error", "message": text}}
            return httpx2.Response(reply, json=body)
        return httpx2.Response(200, json=reply)

    def scout(self) -> ClaudeScout:
        client = anthropic.Anthropic(
            api_key=KEY,
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self)),
        )
        return ClaudeScout(client)


SOUP = {"url": "https://recipes.example/soup", "title": "Red Lentil Soup", "reason": "Freezes."}
HOT = {"url": "https://hot.example/wings", "title": "Fiery Wings", "reason": "Quick."}
COPY = {"url": "https://blog.example/shakshuka", "title": "Shakshuka", "reason": "Eggs."}


def test_scout_request_shape_and_library_tool_loop():
    api = Anthropic(ask_library("lentil soup"), answer(SOUP))
    seen: list[str] = []

    def library(query: str) -> str:
        seen.append(query)
        return "core-026: Misir Wat (dinner, core)"

    result = api.scout().scout("Request: soups", library)
    assert [s.url for s in result.suggestions] == [SOUP["url"]]
    assert seen == ["lentil soup"]

    first = api.bodies[0]
    assert first["model"] == "claude-opus-5"
    tools = {t["name"]: t for t in first["tools"]}
    assert tools["web_search"]["type"] == "web_search_20260209"
    assert tools["web_search"]["blocked_domains"] == BLOCKED_DOMAINS
    assert tools["search_library"]["strict"] is True
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert first["fallbacks"] == "default"
    assert "server-side-fallback" in api.headers[0]["anthropic-beta"]
    # The tool result goes back in the next request, paired with its tool_use id.
    result_block = api.bodies[1]["messages"][-1]["content"][0]
    assert result_block == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "core-026: Misir Wat (dinner, core)",
    }
    assert [c.outcome for c in result.calls] == ["tool", "ok"]
    assert result.calls[-1].cost_usd == pytest.approx(0.01 + 0.03)  # tokens + 3 searches


def test_a_paused_search_is_resumed():
    paused = message([{"type": "text", "text": "Searching..."}], "pause_turn", searches=5)
    api = Anthropic(paused, answer(SOUP))
    result = api.scout().scout("Request: soups", lambda q: "no matches")
    assert result.failure is None and len(result.suggestions) == 1
    assert api.bodies[1]["messages"][-1]["role"] == "assistant"
    assert [c.outcome for c in result.calls] == ["paused", "ok"]


def test_invalid_output_gets_one_retry_then_fails():
    bad = message([{"type": "text", "text": "Here are some ideas!"}], "end_turn")
    api = Anthropic(bad, bad)
    result = api.scout().scout("Request: soups", lambda q: "no matches")
    assert result.failure is not None and result.failure.stage == "validation"
    assert [c.outcome for c in result.calls] == ["retry", "failed"]
    assert "failed validation" in api.bodies[1]["messages"][-1]["content"]


def test_a_refusal_is_a_failure():
    refused = message([], "refusal")
    result = Anthropic(refused).scout().scout("Request", lambda q: "")
    assert result.failure is not None and result.failure.stage == "refusal"


@pytest.mark.parametrize(("status", "message_"), [(401, "rejected the API key"), (400, "credit")])
def test_account_problems_stop(status, message_):
    with pytest.raises(AccountError, match=message_):
        Anthropic(status).scout().scout("Request", lambda q: "")


# --- the core checks what the scout proposes ------------------------------------------------


class FakeScout:
    def __init__(self, *suggestions: dict[str, str], fail: bool = False) -> None:
        self.suggestions = [ScoutSuggestion(**s) for s in suggestions]
        self.fail = fail
        self.library_answers: list[str] = []

    def scout(self, request_text: str, library_search: Callable[[str], str]) -> ScoutResult:
        from mealplan.agents.extractor import CallRecord, Failure

        self.library_answers.append(library_search("shakshuka"))
        call = CallRecord("claude-opus-5", 1000, 200, 5000, "ok", 0.05)
        if self.fail:
            return ScoutResult(calls=[call], failure=Failure("validation", "no JSON"))
        return ScoutResult(suggestions=self.suggestions, calls=[call])


def fetcher(url: str) -> Page:
    if "hot.example" in url:
        return recipe_page(
            url,
            "Fiery Wings",
            ["2 lb chicken wings", "10 habanero peppers, minced", "1/4 cup cayenne pepper"],
        )
    if "shakshuka" in url:
        return recipe_page(
            url,
            "Easy Shakshuka",
            ["2 tablespoons olive oil", "6 large eggs", "1 (28-ounce) can crushed tomatoes"],
        )
    return page("plain", url)


@pytest.fixture
def lib(session, catalog):
    populate_golden(session, catalog)
    return session


def test_scout_suggestions_are_checked_like_pasted_links(lib, catalog):
    """M5 acceptance: suggestions respect every dislike and flag near-duplicates of core."""
    scout = FakeScout(SOUP, HOT, COPY)
    got = scout_candidates(lib, HouseholdPrefs(), catalog, scout, "Request", fetcher=fetcher)
    assert [(c.url, c.verdict) for c in got] == [
        (SOUP["url"], "ok"),
        (HOT["url"], "blocked"),
        (COPY["url"], "duplicate"),
    ]
    assert got[0].note == "Freezes."  # the scout's reason, shown as its words
    assert "core-024" in scout.library_answers[0]
    calls = lib.scalars(select(AgentCall)).all()
    assert [(c.agent, c.cost_usd) for c in calls] == [("web-scout", 0.05)]


def test_a_failed_scout_is_logged_and_reported(lib, catalog):
    with pytest.raises(DiscoveryError, match="no JSON"):
        scout_candidates(lib, HouseholdPrefs(), catalog, FakeScout(fail=True), "Request")
    assert lib.scalars(select(AgentCall)).one().agent == "web-scout"


def test_the_weekly_budget_stops_the_scout(lib, catalog):
    lib.add(AgentCall(agent="pdf-extractor", model="m", outcome="ok", cost_usd=10.0))
    lib.flush()
    scout = FakeScout(SOUP)
    with pytest.raises(BudgetExceeded):
        scout_candidates(lib, HouseholdPrefs(), catalog, scout, "Request")
    assert scout.library_answers == []  # the agent never ran
    # A week later the budget is free again.
    later = datetime(2100, 1, 1)
    assert scout_candidates(
        lib, HouseholdPrefs(), catalog, scout, "Request", fetcher=fetcher, now=later
    )


def test_library_search(lib):
    assert library_search(lib, "shakshuka").startswith("core-024: Easy Shakshuka (dinner")
    assert "core-023" in library_search(lib, "chicken thighs")
    assert library_search(lib, "zorbleberry") == "no matches"


# --- the brief and the chat path ------------------------------------------------------------------


def test_the_brief_carries_the_household_rules():
    prefs = HouseholdPrefs(avoid_ingredients=["cilantro"], max_spice=1)
    text = request_text("Soups", prefs, expiring=["spinach"])
    assert "Never uses: cilantro" in text and "mild at most" in text
    assert "use up soon: spinach" in text and "schema.org Recipe" in text
    assert [p.key for p in presets(prefs)] == ["soups", "lunches", "weeknight"]
    assert "45" in presets(prefs)[2].label
    prompt = chat_prompt("Soups", prefs)
    assert "```json" in prompt and "suggestions" in prompt


def test_parse_link_reply_prefers_the_json_block():
    reply = "Sure!\n```json\n" + json.dumps({"suggestions": [SOUP, SOUP, COPY]}) + "\n```"
    assert [s.url for s in parse_link_reply(reply)] == [SOUP["url"], COPY["url"]]
    assert parse_link_reply(reply)[0].reason == "Freezes."


def test_parse_link_reply_falls_back_to_links_in_text():
    reply = (
        "Try https://recipes.example/soup, or (https://blog.example/shakshuka). "
        "Also see https://recipes.example/soup."
    )
    assert [s.url for s in parse_link_reply(reply)] == [SOUP["url"], COPY["url"]]
    assert parse_link_reply("no links here") == []
