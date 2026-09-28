"""The local extractor (ING-1, NFR-5): Docling reads page images, an Ollama model structures
them. No Ollama or Docling needed: a mock HTTP transport and a fake page reader."""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from mealplan.agents import choose
from mealplan.agents.extractor import (
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    AccountError,
    ExtractionRequest,
    PageContent,
)
from mealplan.agents.local_extractor import (
    DoclingReader,
    LocalStatus,
    OllamaExtractor,
    check_local,
)
from mealplan.web.app import create_app
from tests.test_first_run import HEADERS, TODAY, fresh
from tests.test_first_run import data_dir as data_dir  # fixture

SCAN_TEXT = "Misir Wat\n1 cup red lentils\n2 tablespoons niter kibbeh\nSimmer until soft."


def recipe_json(lines: list[str]) -> str:
    return json.dumps(
        {
            "recipes": [
                {
                    "title": "Misir Wat",
                    "servings": 4,
                    "prep_minutes": None,
                    "cook_minutes": None,
                    "total_minutes": None,
                    "ingredients": lines,
                    "steps": [
                        {
                            "text": "Simmer until soft.",
                            "equipment": ["stove"],
                            "active_minutes": None,
                            "passive_minutes": None,
                        }
                    ],
                    "pages": [7],
                }
            ]
        }
    )


class Ollama:
    """Answers /api/chat with the queued replies and records each request body."""

    def __init__(self, *replies: str | int) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        reply = self.replies.pop(0)
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": "model not found"})
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": reply},
                "eval_count": 120,
                "prompt_eval_count": 900,
                "done": True,
            },
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


class Reader:
    def __init__(self, text: str = SCAN_TEXT) -> None:
        self.text = text
        self.calls = 0

    def read_png(self, png: bytes) -> str:
        self.calls += 1
        return self.text


def scan_request() -> ExtractionRequest:
    return ExtractionRequest(
        [PageContent(number=7, text="", image_png=b"\x89PNG fake")], target_title="Misir Wat"
    )


def test_docling_text_replaces_the_empty_text_layer_and_grounds_the_lines():
    ollama = Ollama(recipe_json(["1 cup red lentils", "2 tablespoons niter kibbeh"]))
    reader = Reader()
    extractor = OllamaExtractor(model="qwen2.5:7b", reader=reader, client=ollama.client())
    result = extractor.extract(scan_request())

    assert result.failure is None and [r.title for r in result.recipes] == ["Misir Wat"]
    assert reader.calls == 1
    body = ollama.bodies[0]
    assert body["model"] == "qwen2.5:7b"
    assert body["format"] == OUTPUT_SCHEMA
    assert body["stream"] is False and body["options"] == {"temperature": 0}
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "1 cup red lentils" in body["messages"][1]["content"]
    assert "images" not in body["messages"][1]
    [call] = result.calls
    assert (call.model, call.cost_usd, call.outcome) == ("ollama:qwen2.5:7b", 0.0, "ok")
    assert (call.input_tokens, call.output_tokens) == (900, 120)


def test_an_invented_ingredient_gets_one_retry_then_fails():
    invented = recipe_json(["1 cup red lentils", "3 cups coconut milk"])
    ollama = Ollama(invented, invented)
    result = OllamaExtractor(reader=Reader(), client=ollama.client()).extract(scan_request())
    assert result.failure is not None and result.failure.stage == "grounding"
    assert [c.outcome for c in result.calls] == ["retry", "failed"]
    assert "failed a check (grounding)" in ollama.bodies[1]["messages"][-1]["content"]


def test_a_retry_can_fix_the_output():
    good = recipe_json(["1 cup red lentils"])
    ollama = Ollama("not json at all", good)
    result = OllamaExtractor(reader=Reader(), client=ollama.client()).extract(scan_request())
    assert result.failure is None and len(result.recipes) == 1
    assert [c.outcome for c in result.calls] == ["retry", "ok"]


def test_text_pages_skip_docling_and_vision_sends_images():
    ollama = Ollama(recipe_json(["1 cup red lentils"]))
    reader = Reader()
    request = ExtractionRequest(
        [
            PageContent(number=7, text=SCAN_TEXT),
            PageContent(number=8, text="", image_png=b"png-bytes"),
        ]
    )
    OllamaExtractor(reader=reader, vision=True, client=ollama.client()).extract(request)
    assert reader.calls == 1  # only the image page
    assert ollama.bodies[0]["messages"][1]["images"] == ["cG5nLWJ5dGVz"]


def test_a_missing_model_stops_with_the_pull_command():
    with pytest.raises(AccountError, match=r"ollama pull llama3\.1:8b"):
        OllamaExtractor(model="llama3.1:8b", reader=Reader(), client=Ollama(404).client()).extract(
            scan_request()
        )


def test_ollama_not_running_stops_the_import():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(AccountError, match="ollama serve"):
        OllamaExtractor(reader=Reader(), client=client).extract(scan_request())


def test_docling_missing_says_how_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "docling.document_converter", None)
    with pytest.raises(AccountError, match="uv sync --extra local"):
        DoclingReader()._load()


def tags(*names: str) -> httpx.Client:
    body = {"models": [{"name": n} for n in names]}
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))


@pytest.mark.parametrize(
    ("models", "docling", "ready", "message"),
    [
        (("qwen2.5:7b", "llama3.1:8b"), True, True, "Ready."),
        (("llama3.1:8b",), True, False, "ollama pull qwen2.5:7b"),
        (("qwen2.5:7b",), False, False, "uv sync --extra local"),
    ],
)
def test_check_local(models, docling, ready, message):
    status = check_local("qwen2.5:7b", client=tags(*models), docling=lambda: docling)
    assert status.ready is ready and message in status.message
    assert status.models == models


def test_check_local_matches_latest_tag():
    assert check_local("mistral", client=tags("mistral:latest"), docling=lambda: True).ready


def test_check_local_when_ollama_is_not_running():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    status = check_local(client=client, docling=lambda: True)
    assert not status.ollama and not status.ready and "ollama.com" in status.message


@pytest.mark.parametrize(
    ("choice", "has_key", "local", "engine"),
    [
        ("auto", True, True, "claude"),
        ("auto", False, True, "local"),
        ("auto", False, False, None),
        ("claude", False, True, None),
        ("local", True, False, "local"),
        ("none", True, True, None),
    ],
)
def test_pick_engine(choice, has_key, local, engine):
    assert choose.pick_engine(choice, has_key, lambda: local) == engine


# --- web and CLI ------------------------------------------------------------------------------

READY = LocalStatus(True, True, True, ("qwen2.5:7b",), "Ready.")
NO_OLLAMA = LocalStatus(False, False, True, (), "Ollama is not answering at http://x.")


def web(tmp_path: Path, data_dir: Path, local: Callable[[], LocalStatus]) -> TestClient:
    settings = fresh(tmp_path).model_copy(update={"data_dir": data_dir})
    app = create_app(settings, today=lambda: TODAY, env_file=tmp_path / ".env", local_checker=local)
    client = TestClient(app, headers=HEADERS)
    assert client.post("/api/login", json={"password": "correct horse"}).status_code == 200
    return client


def test_web_reports_the_local_reader(tmp_path, data_dir):
    with web(tmp_path, data_dir, lambda: READY) as c:
        local = c.get("/api/setup/local").json()
        assert local["ready"] and local["engine"] == "local"
        assert c.get("/api/setup").json()["reader"] == {
            "choice": "auto",
            "local_model": "qwen2.5:7b",
        }


def test_web_reader_choice_is_saved(tmp_path, data_dir):
    with web(tmp_path, data_dir, lambda: NO_OLLAMA) as c:
        assert c.get("/api/setup/local").json()["engine"] is None
        assert c.post("/api/setup/reader", json={"choice": "local"}).status_code == 200
        assert c.get("/api/setup/local").json()["engine"] == "local"
        assert c.post("/api/setup/reader", json={"choice": "gpt"}).status_code == 422
    assert "MEALPLAN_EXTRACTOR=local" in (tmp_path / ".env").read_text()


def test_cli_engine_option(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mealplan.cli import app

    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MEALPLAN_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MEALPLAN_DATA_DIR", str(Path("data").resolve()))
    monkeypatch.chdir(tmp_path)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    runner = CliRunner()
    bad = runner.invoke(app, ["import", "pdf", str(pdf), "--engine", "gpt"])
    assert bad.exit_code == 1 and "--engine must be one of" in bad.output
    no_key = runner.invoke(app, ["import", "pdf", str(pdf), "--engine", "claude"])
    assert no_key.exit_code == 1 and "needs an API key" in no_key.output


@pytest.mark.local
def test_docling_reads_a_rendered_page():
    """With `uv sync --extra local` and its models downloaded: real OCR on a rendered page."""
    pytest.importorskip("docling")
    import pymupdf

    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    page.insert_text((50, 72), "Lentil Soup\n1 cup red lentils\n1 onion, diced", fontsize=16)
    png = page.get_pixmap(dpi=150).tobytes("png")  # type: ignore[no-untyped-call]
    text = DoclingReader().read_png(png)
    assert "red lentils" in text.lower()
