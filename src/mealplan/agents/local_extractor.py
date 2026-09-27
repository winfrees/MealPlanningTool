"""A local recipe extractor: Docling reads the pages, a model served by Ollama structures them
(ING-1 without a cloud API; NFR-5 local-first).

Same `RecipeExtractor` protocol, output schema, checks and retry as the Claude extractor:
- Pages with an image (scans, screenshots, designed booklets) are read by Docling, whose OCR
  and layout analysis replace the PDF's text layer for that page. Web-print pages keep their
  text layer. Grounding then checks each ingredient line against that text, as before.
- The text goes to Ollama's /api/chat with the output JSON schema as `format`, temperature 0.
  A vision model can also be sent the page images (`vision=True`).
- Nothing leaves the computer. Calls are logged with cost 0 (NFR-7).

Docling is an optional extra (`uv sync --extra local`); without it, image pages fall back to
whatever text layer they have, and `check_local` says how to install it.
"""

import base64
import io
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from mealplan.agents.extractor import (
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    AccountError,
    CallRecord,
    ExtractionRequest,
    ExtractionResult,
    Failure,
    check_output,
)

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
TIMEOUT_SECONDS = 600.0  # a local model on a CPU can take minutes per recipe


class PageReader(Protocol):
    def read_png(self, png: bytes) -> str: ...


class DoclingReader:
    """OCR and layout for one page image, as Markdown. The converter loads its models once."""

    def __init__(self) -> None:
        self._converter: Any = None

    def _load(self) -> Any:
        if self._converter is None:
            try:
                from docling.document_converter import DocumentConverter
            except ImportError:
                raise AccountError(
                    "Docling is not installed; run `uv sync --extra local` and restart"
                ) from None
            self._converter = DocumentConverter()
        return self._converter

    def read_png(self, png: bytes) -> str:
        from docling.datamodel.base_models import DocumentStream

        converter = self._load()
        try:
            result = converter.convert(DocumentStream(name="page.png", stream=io.BytesIO(png)))
        except Exception as e:  # Docling raises many types; all mean the pages can't be read
            raise AccountError(
                f"Docling could not read a page ({type(e).__name__}: {str(e)[:200]}). Its first "
                "run downloads OCR and layout models (modelscope.cn, huggingface.co); check the "
                "internet connection, then import again"
            ) from e
        text: str = result.document.export_to_markdown()
        return text


def docling_installed() -> bool:
    try:
        import docling.document_converter  # noqa: F401
    except ImportError:
        return False
    return True


def _user_text(request: ExtractionRequest) -> str:
    parts = [f"--- Page {p.number} ---\n{p.text.strip() or '(no text)'}" for p in request.pages]
    ask = (
        f'Extract only the recipe titled "{request.target_title}".'
        if request.target_title
        else "Extract every recipe on these pages."
    )
    return "\n\n".join([*parts, ask, "Reply with JSON only."])


class OllamaExtractor:
    """`RecipeExtractor` backed by Docling and an Ollama model."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        url: str = DEFAULT_URL,
        reader: PageReader | None = None,
        vision: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.label = f"local model ({model})"
        self.url = url.rstrip("/")
        self.reader = reader if reader is not None else DoclingReader()
        self.vision = vision
        self.client = client or httpx.Client(timeout=TIMEOUT_SECONDS)

    def read_pages(self, request: ExtractionRequest) -> ExtractionRequest:
        """Replace each image page's text layer with what Docling reads from the image."""
        pages = []
        for page in request.pages:
            if page.image_png is not None:
                text = self.reader.read_png(page.image_png)
                if len(text.strip()) >= len(page.text.strip()) // 2:
                    page = replace(page, text=text)
            pages.append(page)
        return replace(request, pages=pages)

    def _chat(self, messages: list[dict[str, Any]]) -> tuple[str, CallRecord]:
        start = time.monotonic()
        try:
            response = self.client.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "format": OUTPUT_SCHEMA,
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
        except httpx.ConnectError:
            raise AccountError(
                f"could not reach Ollama at {self.url}; is it running (`ollama serve`)?"
            ) from None
        if response.status_code == 404:
            raise AccountError(
                f"Ollama has no model {self.model!r}; run `ollama pull {self.model}`"
            )
        response.raise_for_status()
        body = response.json()
        record = CallRecord(
            model=f"ollama:{self.model}",
            input_tokens=int(body.get("prompt_eval_count") or 0),
            output_tokens=int(body.get("eval_count") or 0),
            latency_ms=int((time.monotonic() - start) * 1000),
            outcome="ok",
            cost_usd=0.0,
        )
        return str(body.get("message", {}).get("content", "")), record

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        result = ExtractionResult()
        request = self.read_pages(request)
        user: dict[str, Any] = {"role": "user", "content": _user_text(request)}
        if self.vision:
            user["images"] = [
                base64.b64encode(p.image_png).decode("ascii")
                for p in request.pages
                if p.image_png is not None
            ]
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, user]
        for attempt in range(2):
            try:
                text, record = self._chat(messages)
            except httpx.HTTPError as e:
                result.failure = Failure("api", f"{type(e).__name__}: {e}")
                return result
            output, failure = check_output(text, request)
            if failure is None:
                result.calls.append(record)
                result.recipes = output.recipes
                return result
            if attempt == 0:
                result.calls.append(replace(record, outcome="retry"))
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": f"That output failed a check ({failure.stage}): "
                        f"{failure.error}\nReturn the corrected JSON.",
                    }
                )
            else:
                result.calls.append(replace(record, outcome="failed"))
                result.failure = failure
        return result


# --- checking the setup -----------------------------------------------------------------------


@dataclass(frozen=True)
class LocalStatus:
    ollama: bool
    model: bool
    docling: bool
    models: tuple[str, ...]
    message: str

    @property
    def ready(self) -> bool:
        return self.ollama and self.model and self.docling


def _same_model(a: str, b: str) -> bool:
    def norm(name: str) -> str:
        return name if ":" in name else f"{name}:latest"

    return norm(a) == norm(b)


def check_local(
    model: str = DEFAULT_MODEL,
    url: str = DEFAULT_URL,
    client: httpx.Client | None = None,
    docling: Callable[[], bool] = docling_installed,
) -> LocalStatus:
    """Is Ollama running, is the model pulled, is Docling installed? Never raises."""
    has_docling = docling()
    try:
        response = (client or httpx.Client(timeout=3.0)).get(f"{url.rstrip('/')}/api/tags")
        response.raise_for_status()
        models = tuple(m.get("name", "") for m in response.json().get("models", []))
    except (httpx.HTTPError, json.JSONDecodeError, AttributeError):
        return LocalStatus(
            False,
            False,
            has_docling,
            (),
            f"Ollama is not answering at {url}. Install it from ollama.com and start it.",
        )
    has_model = any(_same_model(m, model) for m in models)
    problems = []
    if not has_model:
        problems.append(f"run `ollama pull {model}`")
    if not has_docling:
        problems.append("run `uv sync --extra local` for Docling")
    message = "Ready." if not problems else "Ollama is running; " + " and ".join(problems) + "."
    return LocalStatus(True, has_model, has_docling, models, message)
