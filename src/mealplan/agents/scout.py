"""The web recipe scout (M5, requirements §5): Claude searches the web and proposes recipe links.

The scout only *proposes*: it returns URLs with a short reason. The core then fetches each page
itself, parses its structured recipe data and checks it against the household's rules and
library (`core/discovery.py`); nothing the scout says about a recipe is trusted. Tools: the
`web_search` server tool and one read-only client tool, `search_library`, so it can avoid
dishes the household already has. The same brief also produces a prompt for a claude.ai chat
(no API key), whose pasted reply is read with `parse_link_reply`.
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
from anthropic.types.beta import BetaMessage, BetaMessageParam
from pydantic import BaseModel, ConfigDict, ValidationError

from mealplan.agents.extractor import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    AccountError,
    CallRecord,
    Failure,
    _inline_refs,
    cost_usd,
)
from mealplan.core.preferences import HouseholdPrefs
from mealplan.models.enums import MealRole

AGENT_NAME = "web-scout"
MAX_TURNS = 8  # model calls per scout run, including pause_turn resumes and one retry
MAX_SEARCHES = 8
WEB_SEARCH_USD = 0.01  # per search request
BLOCKED_DOMAINS = [
    "pinterest.com",
    "youtube.com",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "reddit.com",
]


class ScoutSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    reason: str


class ScoutOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[ScoutSuggestion]


OUTPUT_SCHEMA = _inline_refs(ScoutOutput.model_json_schema())


# --- the brief ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    request: str
    role: MealRole


def presets(prefs: HouseholdPrefs) -> list[Preset]:
    """The first briefs (the collection is dinner-heavy; lunches come from Sunday prep)."""
    return [
        Preset(
            "soups",
            "Soups for Sunday prep",
            "Batch soups to cook on Sunday that keep 4 to 5 days in the fridge or freeze well, "
            "for packed weekday lunches.",
            MealRole.SOUP,
        ),
        Preset(
            "lunches",
            "Grain salads & lunch bowls",
            "Grain salads and lunch bowls that are made ahead on Sunday and still taste good "
            "packed cold on Thursday.",
            MealRole.LUNCH,
        ),
        Preset(
            "weeknight",
            f"Weeknight dinners ≤ {prefs.max_weeknight_active_minutes} min",
            f"Weeknight dinners with at most {prefs.max_weeknight_active_minutes} minutes of "
            "hands-on work that make good leftovers for the next day's lunch.",
            MealRole.DINNER,
        ),
    ]


def household_brief(
    prefs: HouseholdPrefs, expiring: list[str] | None = None, count: int = 8
) -> str:
    spice = ["no heat", "mild", "medium", "any heat"][prefs.max_spice]
    lines = [
        f"- Cooks for {prefs.dinner_servings:g}; dinners often become next-day lunches for "
        f"{prefs.lunch_servings:g}.",
        f"- Spice: {spice} at most.",
    ]
    if prefs.avoid_ingredients:
        lines.append(f"- Never uses: {', '.join(prefs.avoid_ingredients)}.")
    if prefs.avoid_tags:
        lines.append(f"- Avoids: {', '.join(t.replace('-', ' ') for t in prefs.avoid_tags)}.")
    if expiring:
        lines.append(f"- Would like to use up soon: {', '.join(expiring)}.")
    return (
        "Household rules:\n" + "\n".join(lines) + f"\n\nFind {count} recipes. Each must be a "
        "single recipe page (not a roundup or a video) on a site that publishes schema.org "
        "Recipe data (most recipe sites and food magazines do); avoid paywalled sites. Prefer "
        "variety in protein and cuisine."
    )


SYSTEM_PROMPT = """\
You find recipes on the web for a household meal planner. You propose links; the planner then \
fetches each page itself, reads its structured recipe data, and checks it against the \
household's rules and recipe library, so give the direct recipe page URL you found, not a \
guess. Before suggesting a dish, use search_library to see whether the household already has \
something like it, and skip close matches. Keep each reason to one sentence about why it fits \
the request."""


def request_text(request: str, prefs: HouseholdPrefs, expiring: list[str] | None = None) -> str:
    return f"Request: {request.strip()}\n\n{household_brief(prefs, expiring)}"


def chat_prompt(request: str, prefs: HouseholdPrefs, expiring: list[str] | None = None) -> str:
    """The same brief for a claude.ai chat, when there is no API key."""
    example = json.dumps(
        {
            "suggestions": [
                {
                    "url": "https://www.example.com/recipes/red-lentil-soup",
                    "title": "Red Lentil Soup",
                    "reason": "Freezes well and packs for lunch.",
                }
            ]
        },
        indent=2,
    )
    return (
        "Please search the web for recipes for my meal planner.\n\n"
        f"{request_text(request, prefs, expiring)}\n\n"
        "Give the direct link to each recipe page you actually found. Reply with only one "
        f"JSON code block in this shape:\n\n```json\n{example}\n```"
    )


# --- the agent ----------------------------------------------------------------------------------


@dataclass
class ScoutResult:
    suggestions: list[ScoutSuggestion] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    failure: Failure | None = None


class RecipeScout(Protocol):
    def scout(self, request_text: str, library_search: Callable[[str], str]) -> ScoutResult: ...


LIBRARY_TOOL: dict[str, Any] = {
    "name": "search_library",
    "description": (
        "Search the household's recipe library by dish name or main ingredient. Returns the "
        "closest recipes (ref, title, meal) or 'no matches'. Read-only."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "A dish or ingredient."}},
        "required": ["query"],
        "additionalProperties": False,
    },
}


TOOLS: list[Any] = [
    {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": MAX_SEARCHES,
        "blocked_domains": BLOCKED_DOMAINS,
    },
    LIBRARY_TOOL,
]
OUTPUT_CONFIG: Any = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}


def _texts(message: BetaMessage) -> list[str]:
    return [b.text for b in message.content if b.type == "text"]


def parse_output(texts: list[str]) -> tuple[ScoutOutput | None, str]:
    """The structured answer: the last text block that validates (earlier ones may be
    commentary between searches), else all text joined."""
    for candidate in [*reversed(texts), "".join(texts)]:
        try:
            return ScoutOutput.model_validate_json(candidate.strip()), ""
        except ValidationError as e:
            error = str(e)
    return None, error if texts else "no answer"


class ClaudeScout:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.label = f"Claude ({model})"

    def _call(self, messages: list[BetaMessageParam]) -> tuple[BetaMessage, CallRecord]:
        start = time.monotonic()
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOLS,
                output_config=OUTPUT_CONFIG,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise AccountError(
                f"Anthropic rejected the API key ({type(e).__name__}); "
                "check it on the Setup page or in .env"
            ) from e
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower():
                raise AccountError(
                    "the Anthropic account is out of credit; add credit at "
                    "console.anthropic.com (Settings, Billing)"
                ) from e
            raise
        usage = response.usage
        server = getattr(usage, "server_tool_use", None)
        searches = int(getattr(server, "web_search_requests", 0) or 0)
        record = CallRecord(
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
            outcome="ok",
            cost_usd=round(
                cost_usd(response.model, usage.input_tokens, usage.output_tokens)
                + searches * WEB_SEARCH_USD,
                6,
            ),
        )
        return response, record

    def scout(self, request_text: str, library_search: Callable[[str], str]) -> ScoutResult:
        result = ScoutResult()
        messages: list[BetaMessageParam] = [{"role": "user", "content": request_text}]
        retried = False
        for _ in range(MAX_TURNS):
            try:
                response, record = self._call(messages)
            except anthropic.APIError as e:
                result.failure = Failure("api", f"{type(e).__name__}: {e}")
                return result
            if response.stop_reason == "refusal":
                result.calls.append(_outcome(record, "refusal"))
                result.failure = Failure("refusal", "the model declined this request")
                return result
            if response.stop_reason == "pause_turn":
                # A long server-side search paused: send the turn back and it resumes.
                result.calls.append(_outcome(record, "paused"))
                messages.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason == "tool_use":
                result.calls.append(_outcome(record, "tool"))
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": _run_library_tool(block.input, library_search),
                            }
                            for block in response.content
                            if block.type == "tool_use"
                        ],
                    }
                )
                continue
            output, error = parse_output(_texts(response))
            if output is not None:
                result.calls.append(record)
                result.suggestions = output.suggestions
                return result
            if retried or response.stop_reason == "max_tokens":
                result.calls.append(_outcome(record, "failed"))
                result.failure = Failure("validation", error)
                return result
            retried = True
            result.calls.append(_outcome(record, "retry"))
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": f"That answer failed validation: {error}\nReturn the JSON only.",
                }
            )
        result.failure = Failure("api", f"no answer after {MAX_TURNS} calls")
        return result


def _outcome(record: CallRecord, outcome: str) -> CallRecord:
    return CallRecord(**{**record.__dict__, "outcome": outcome})


def _run_library_tool(tool_input: Any, library_search: Callable[[str], str]) -> str:
    query = tool_input.get("query", "") if isinstance(tool_input, dict) else ""
    return library_search(str(query)) if query else "no query given"


# --- a pasted chat reply (no API key) -----------------------------------------------------------

_LINK = re.compile(r"https?://[^\s<>\"'`\])]+")


def parse_link_reply(reply: str) -> list[ScoutSuggestion]:
    """Links from a pasted claude.ai reply: its JSON block if there is one, else every web link
    in the text. The text is data; nothing in it is followed as an instruction."""
    found: list[ScoutSuggestion] = []
    block = re.search(r"```(?:json)?\s*(.*?)```", reply, flags=re.DOTALL | re.IGNORECASE)
    for text in [block.group(1) if block else "", reply]:
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            continue
        items = data.get("suggestions", []) if isinstance(data, dict) else data
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                found.append(
                    ScoutSuggestion(
                        url=item["url"].strip(),
                        title=str(item.get("title", "")),
                        reason=str(item.get("reason", "")),
                    )
                )
        if found:
            break
    if not found:
        for url in _LINK.findall(reply):
            found.append(ScoutSuggestion(url=url.rstrip(".,;:!?"), title="", reason=""))
    unique: dict[str, ScoutSuggestion] = {}
    for s in found:
        unique.setdefault(s.url, s)
    return list(unique.values())
