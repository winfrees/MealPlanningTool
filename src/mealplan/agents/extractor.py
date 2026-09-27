"""PDF recipe extractor agent (ING-1): page text + page images -> recipes, with page refs.

Guardrails (requirements §5):
- Schema-first: output is constrained by a JSON schema and validated with Pydantic.
- One retry with the error, then the caller records a failure (failed queue).
- Grounding: every ingredient line must appear in the page's text layer.
- No writes: this returns data; the ingest pipeline decides what to store.
"""

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
from anthropic.types.beta import BetaMessage, BetaMessageParam
from pydantic import BaseModel, ConfigDict, ValidationError

from mealplan.ingest.grounding import ungrounded_lines

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens (input, output). Unknown models are logged at 0 and flagged.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedStep(_Strict):
    text: str
    equipment: list[str]
    active_minutes: int | None
    passive_minutes: int | None


class ExtractedRecipe(_Strict):
    title: str
    servings: float | None
    prep_minutes: int | None
    cook_minutes: int | None
    total_minutes: int | None
    ingredients: list[str]
    steps: list[ExtractedStep]
    pages: list[int]


class ExtractionOutput(_Strict):
    recipes: list[ExtractedRecipe]


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[1]])
            return {
                k: ({p: resolve(sub) for p, sub in v.items()} if k == "properties" else resolve(v))
                for k, v in node.items()
                if k != "title"  # schema metadata; a property named "title" is kept above
            }
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    result: dict[str, Any] = resolve(schema)
    return result


OUTPUT_SCHEMA = _inline_refs(ExtractionOutput.model_json_schema())

SYSTEM_PROMPT = """\
You extract recipes from pages of a household's recipe collection: printed web pages, \
designed booklets, and phone screenshots pasted into a notebook. Each page comes with its \
text layer (may be empty or out of order) and sometimes an image of the page.

Return every recipe on the pages, or only the one named in the request if a title is given.
- ingredients: one entry per ingredient, copied exactly as written in the text layer when \
the text layer has it (same numbers, units, words, and notes), so each line can be found in \
the source. Leave out section headings such as "For the sauce:". For image-only pages, \
transcribe what the image shows.
- steps: in order, each as written. Set equipment (oven, stove, slow cooker, pressure \
cooker, grill, microwave, mixer) and active/passive minutes only when the page states them \
or they are plain from the step; otherwise use an empty list or null.
- servings and times: only as stated on the page; otherwise null.
- pages: the page numbers the recipe appears on.
Ignore ads, comments, navigation, ratings, and nutrition panels. If there is no recipe, \
return an empty list."""


@dataclass(frozen=True)
class PageContent:
    number: int
    text: str
    image_png: bytes | None = None


@dataclass(frozen=True)
class ExtractionRequest:
    pages: list[PageContent]
    target_title: str | None = None


@dataclass(frozen=True)
class CallRecord:
    """One API call, for the agent call log (NFR-7)."""

    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    outcome: str  # ok, retry, failed, refusal
    cost_usd: float


@dataclass(frozen=True)
class Failure:
    stage: str  # validation, grounding, refusal, api
    error: str
    raw_output: str = ""


@dataclass
class ExtractionResult:
    recipes: list[ExtractedRecipe] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    failure: Failure | None = None


class RecipeExtractor(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


def check_output(text: str, request: ExtractionRequest) -> tuple[ExtractionOutput, Failure | None]:
    """Validate the schema, then grounding against the text layer. Pure, so it is testable."""
    try:
        output = ExtractionOutput.model_validate_json(text)
    except ValidationError as e:
        return ExtractionOutput(recipes=[]), Failure("validation", str(e), text)
    missing = []
    for r in output.recipes:
        own = [p for p in request.pages if p.number in r.pages] or request.pages
        missing += ungrounded_lines(r.ingredients, "\n".join(p.text for p in own))
    if missing:
        listed = "; ".join(repr(m) for m in missing[:10])
        return output, Failure(
            "grounding", f"ingredient lines not in the text layer: {listed}", text
        )
    return output, None


def _user_content(request: ExtractionRequest) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for page in request.pages:
        text = page.text.strip() or "(no text layer)"
        content.append({"type": "text", "text": f"--- Page {page.number} text layer ---\n{text}"})
        if page.image_png is not None:
            content.append({"type": "text", "text": f"--- Page {page.number} image ---"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(page.image_png).decode("ascii"),
                    },
                }
            )
    ask = (
        f'Extract only the recipe titled "{request.target_title}".'
        if request.target_title
        else "Extract every recipe on these pages."
    )
    content.append({"type": "text", "text": ask})
    return content


class ClaudeExtractor:
    """Claude-backed extractor. Uses server-side refusal fallbacks (`fallbacks="default"`)."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 16000,
    ) -> None:
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def _call(self, messages: list[BetaMessageParam]) -> tuple[BetaMessage, int]:
        start = time.monotonic()
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        return response, int((time.monotonic() - start) * 1000)

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        result = ExtractionResult()
        messages: list[BetaMessageParam] = [
            {"role": "user", "content": _user_content(request)}  # type: ignore[typeddict-item]
        ]
        for attempt in range(2):
            try:
                response, latency = self._call(messages)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
                raise AccountError(
                    f"Anthropic rejected the API key ({type(e).__name__}); "
                    "check it on the Get started page or in .env"
                ) from e
            except anthropic.BadRequestError as e:
                if "credit balance" in str(e).lower():
                    raise AccountError(
                        "the Anthropic account is out of credit; add credit at "
                        "console.anthropic.com (Settings, Billing)"
                    ) from e
                result.failure = Failure("api", f"{type(e).__name__}: {e}")
                return result
            except anthropic.APIError as e:
                result.failure = Failure("api", f"{type(e).__name__}: {e}")
                return result

            usage = response.usage
            record = CallRecord(
                model=response.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=latency,
                outcome="ok",
                cost_usd=cost_usd(response.model, usage.input_tokens, usage.output_tokens),
            )

            if response.stop_reason == "refusal":
                detail = response.stop_details.category if response.stop_details else None
                result.calls.append(_with_outcome(record, "refusal"))
                result.failure = Failure("refusal", f"model declined (category: {detail})")
                return result

            text = next((b.text for b in response.content if b.type == "text"), "")
            failure: Failure | None
            if response.stop_reason == "max_tokens":
                output, failure = (
                    ExtractionOutput(recipes=[]),
                    Failure("validation", "output truncated at max_tokens", text),
                )
            else:
                output, failure = check_output(text, request)

            if failure is None:
                result.calls.append(record)
                result.recipes = output.recipes
                return result

            if attempt == 0:
                result.calls.append(_with_outcome(record, "retry"))
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": f"That output failed a check ({failure.stage}): "
                        f"{failure.error}\nReturn the corrected JSON.",
                    }
                )
            else:
                result.calls.append(_with_outcome(record, "failed"))
                result.failure = failure
        return result


class AccountError(RuntimeError):
    """A problem with the API key or account: every call would fail the same way, so an
    import stops instead of recording a failure per recipe."""


def _with_outcome(record: CallRecord, outcome: str) -> CallRecord:
    return CallRecord(**{**record.__dict__, "outcome": outcome})


def output_json(recipes: list[ExtractedRecipe]) -> str:
    """Serialize extractor output (used by evals and fakes)."""
    return json.dumps({"recipes": [r.model_dump() for r in recipes]})
