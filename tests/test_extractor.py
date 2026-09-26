"""PDF extractor agent guardrails, against a fake Anthropic client (no API calls)."""

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import pytest

from mealplan.agents.extractor import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    OUTPUT_SCHEMA,
    ClaudeExtractor,
    ExtractionRequest,
    PageContent,
    check_output,
    cost_usd,
)
from mealplan.ingest.grounding import normalize, ungrounded_lines

PAGE_TEXT = """Misir Wat
Ingredients
1 cup red lentils
2 tablespoons niter kibbeh
1 onion, finely chopped
Instructions
Simmer everything for 30 minutes."""

GOOD = {
    "recipes": [
        {
            "title": "Misir Wat",
            "servings": 4,
            "prep_minutes": None,
            "cook_minutes": 30,
            "total_minutes": None,
            "ingredients": [
                "1 cup red lentils",
                "2 tablespoons niter kibbeh",
                "1 onion, finely chopped",
            ],
            "steps": [
                {
                    "text": "Simmer everything for 30 minutes.",
                    "equipment": ["stove"],
                    "active_minutes": 5,
                    "passive_minutes": 25,
                }
            ],
            "pages": [41],
        }
    ]
}
UNGROUNDED = json.loads(json.dumps(GOOD))
UNGROUNDED["recipes"][0]["ingredients"].append("3 cups vegetable broth")


def response(
    payload: Any = None,
    stop_reason: str = "end_turn",
    model: str = DEFAULT_MODEL,
    category: str | None = None,
) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if stop_reason == "refusal" else None,
        model=model,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def extractor(responses: list[Any]) -> tuple[ClaudeExtractor, FakeMessages]:
    messages = FakeMessages(responses)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeExtractor(client=client), messages  # type: ignore[arg-type]


REQUEST = ExtractionRequest(pages=[PageContent(41, PAGE_TEXT)], target_title="Misir Wat")


def test_success_and_request_shape():
    ex, fake = extractor([response(GOOD)])
    result = ex.extract(REQUEST)
    assert result.failure is None
    assert [r.title for r in result.recipes] == ["Misir Wat"]
    assert [c.outcome for c in result.calls] == ["ok"]
    assert result.calls[0].cost_usd == pytest.approx(0.01)  # 1000 * $5/M + 200 * $25/M

    kwargs = fake.calls[0]
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    content = kwargs["messages"][0]["content"]
    assert 'titled "Misir Wat"' in content[-1]["text"]


def test_page_images_are_sent():
    ex, fake = extractor([response(GOOD)])
    ex.extract(ExtractionRequest(pages=[PageContent(89, "", image_png=b"\x89PNG")]))
    blocks = fake.calls[0]["messages"][0]["content"]
    image = next(b for b in blocks if b["type"] == "image")
    assert image["source"]["media_type"] == "image/png"


def test_validation_error_retries_once_with_the_error():
    ex, fake = extractor([response("{not json"), response(GOOD)])
    result = ex.extract(REQUEST)
    assert result.failure is None
    assert [c.outcome for c in result.calls] == ["retry", "ok"]
    retry_messages = fake.calls[1]["messages"]
    assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
    assert "failed a check (validation)" in retry_messages[-1]["content"]


def test_second_failure_goes_to_the_failed_queue():
    ex, _ = extractor([response(UNGROUNDED), response(UNGROUNDED)])
    result = ex.extract(REQUEST)
    assert result.failure is not None
    assert result.failure.stage == "grounding"
    assert "3 cups vegetable broth" in result.failure.error
    assert [c.outcome for c in result.calls] == ["retry", "failed"]
    assert result.recipes == []


def test_truncated_output_is_a_validation_failure():
    ex, _ = extractor([response("{", stop_reason="max_tokens"), response(GOOD)])
    result = ex.extract(REQUEST)
    assert result.failure is None
    assert [c.outcome for c in result.calls] == ["retry", "ok"]


def test_refusal_is_not_retried():
    ex, fake = extractor([response("", stop_reason="refusal", category="cyber")])
    result = ex.extract(REQUEST)
    assert result.failure is not None and result.failure.stage == "refusal"
    assert "cyber" in result.failure.error
    assert len(fake.calls) == 1


def test_api_error_is_reported_not_raised():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    ex, _ = extractor([anthropic.APIConnectionError(request=request)])
    result = ex.extract(REQUEST)
    assert result.failure is not None and result.failure.stage == "api"


def test_schema_is_closed_and_self_contained():
    text = json.dumps(OUTPUT_SCHEMA)
    assert "$ref" not in text and "$defs" not in text

    def objects(node: Any) -> list[dict[str, Any]]:
        found = []
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append(node)
            for v in node.values():
                found += objects(v)
        elif isinstance(node, list):
            for v in node:
                found += objects(v)
        return found

    for obj in objects(OUTPUT_SCHEMA):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_check_output_grounds_each_recipe_on_its_own_pages():
    request = ExtractionRequest(
        pages=[PageContent(41, PAGE_TEXT), PageContent(89, "", image_png=b"x")]
    )
    vision = json.loads(json.dumps(GOOD))
    vision["recipes"][0]["pages"] = [89]
    vision["recipes"][0]["ingredients"] = ["4 chicken breasts (from the screenshot)"]
    _, failure = check_output(json.dumps(vision), request)
    assert failure is None  # image-only page cannot be grounded; reviewer sees the tag


def test_cost_for_unknown_model_is_zero():
    assert cost_usd("some-future-model", 10, 10) == 0.0


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("▢ 1½ cups teff flour", "1 1/2 cups teff flour"),
        ("1 ½ cups Teff Flour,", "1 1/2 cups teff flour"),
        ("2 (15-ounce) cans chickpeas", "2 15-ounce cans chickpeas"),
    ],
)
def test_normalize(line, expected):
    assert normalize(line) == expected


def test_ungrounded_lines():
    assert ungrounded_lines(["1 cup red lentils", "1 onion, finely chopped"], PAGE_TEXT) == []
    assert ungrounded_lines(["1 cup green lentils"], PAGE_TEXT) == ["1 cup green lentils"]
    assert ungrounded_lines(["anything"], "") == []
