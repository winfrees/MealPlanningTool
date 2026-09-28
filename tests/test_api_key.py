"""The Anthropic API key (UI-8, NFR-6): where it is read from, tidying a pasted key, checking it,
and stopping an import when the key is rejected. No real API calls: a mock HTTP transport."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from mealplan.agents.credentials import KeyProblem, check_key, clean_key
from mealplan.agents.extractor import AccountError, ClaudeExtractor, ExtractionRequest, PageContent
from mealplan.config import Settings
from mealplan.web.app import create_app
from tests.test_first_run import HEADERS, TODAY, fresh, wait_for_import
from tests.test_first_run import data_dir as data_dir  # fixture
from tests.test_pdf_import import make_pdf

KEY = "sk-ant-api03-" + "x" * 40


def mock_client(status: int, body: dict[str, Any] | None = None) -> anthropic.Anthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=body or {"error": {"message": "nope"}})

    return anthropic.Anthropic(
        api_key=KEY,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "line",
    [
        "ANTHROPIC_API_KEY=sk-ant-a",
        "MEALPLAN_ANTHROPIC_API_KEY=sk-ant-a",
        'ANTHROPIC_API_KEY = "sk-ant-a"',
    ],
)
def test_key_is_read_from_env_file_under_either_name(tmp_path, monkeypatch, line):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MEALPLAN_ANTHROPIC_API_KEY", raising=False)
    Path(".env").write_text(line + "\n")
    key = Settings().anthropic_api_key
    assert key is not None and key.get_secret_value() == "sk-ant-a"


@pytest.mark.parametrize(
    "pasted",
    [
        KEY,
        f"  {KEY}\n",
        f'"{KEY}"',
        f"ANTHROPIC_API_KEY={KEY}",
        f"MEALPLAN_ANTHROPIC_API_KEY = {KEY}",
    ],
)
def test_clean_key_accepts_common_pastes(pasted):
    assert clean_key(pasted) == KEY


@pytest.mark.parametrize("pasted", ["", "claude-opus-5", "https://api.anthropic.com", "sk-ant- x"])
def test_clean_key_explains_what_a_key_looks_like(pasted):
    with pytest.raises(KeyProblem):
        clean_key(pasted)
    with pytest.raises(KeyProblem, match="sk-ant-"):
        clean_key("claude-opus-5")


def test_check_key_passes_when_the_model_is_visible():
    check_key(
        mock_client(
            200,
            {
                "id": "claude-opus-5",
                "type": "model",
                "display_name": "x",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
    )


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "rejected that key"), (403, "cannot use"), (404, "not available"), (500, "500")],
)
def test_check_key_says_what_is_wrong(status, message):
    with pytest.raises(KeyProblem, match=message):
        check_key(mock_client(status))


def test_check_key_without_a_network():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("offline", request=request)

    client = anthropic.Anthropic(
        api_key=KEY,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(KeyProblem, match="could not reach"):
        check_key(client)


def extract_with(client: anthropic.Anthropic) -> None:
    ClaudeExtractor(client).extract(
        ExtractionRequest([PageContent(number=1, text="Soup\n1 onion")], target_title="Soup")
    )


def test_a_rejected_key_stops_the_extractor():
    with pytest.raises(AccountError, match="rejected the API key"):
        extract_with(mock_client(401))


def test_no_credit_stops_the_extractor():
    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Your credit balance is too low"},
    }
    with pytest.raises(AccountError, match="out of credit"):
        extract_with(mock_client(400, body))


# --- the web app -----------------------------------------------------------------------------


def app_client(
    tmp_path: Path, data_dir: Path, checker: Callable[[str], None], **extra: Any
) -> TestClient:
    settings = fresh(tmp_path).model_copy(update={"data_dir": data_dir})
    app = create_app(
        settings, today=lambda: TODAY, key_checker=checker, env_file=tmp_path / ".env", **extra
    )
    client = TestClient(app, headers=HEADERS)
    assert client.post("/api/login", json={"password": "correct horse"}).status_code == 200
    return client


@pytest.fixture
def client(tmp_path: Path, data_dir: Path) -> Iterator[TestClient]:
    checked: list[str] = []
    with app_client(tmp_path, data_dir, checked.append) as c:
        c.checked = checked  # type: ignore[attr-defined]
        yield c


def test_pasting_a_key_checks_and_saves_it(client, tmp_path):
    (tmp_path / ".env").write_text("MEALPLAN_WEB_PASSWORD=correct horse\nANTHROPIC_API_KEY=old\n")
    assert client.get("/api/setup").json()["api_key"] == {"set": False, "ends_with": None}
    response = client.post("/api/setup/api-key", json={"key": f"ANTHROPIC_API_KEY={KEY}"})
    assert response.status_code == 200, response.text
    assert response.json() == {"set": True, "ends_with": "xxxx"}
    assert client.checked == [KEY]
    # One key line, under the app's own name; the password line is kept.
    assert (tmp_path / ".env").read_text() == (
        f"MEALPLAN_WEB_PASSWORD=correct horse\nMEALPLAN_ANTHROPIC_API_KEY={KEY}\n"
    )
    assert client.post("/api/setup/api-key/check").json()["ok"] is True


def test_a_model_name_is_not_a_key(client, tmp_path):
    response = client.post("/api/setup/api-key", json={"key": "claude-opus-5"})
    assert response.status_code == 400 and "sk-ant-" in response.json()["detail"]
    assert not (tmp_path / ".env").exists()


def test_a_rejected_key_is_not_saved(tmp_path, data_dir):
    def reject(key: str) -> None:
        raise KeyProblem("Anthropic rejected that key")

    with app_client(tmp_path, data_dir, reject) as c:
        response = c.post("/api/setup/api-key", json={"key": KEY})
        assert response.status_code == 400 and "rejected" in response.json()["detail"]
        assert c.get("/api/setup").json()["api_key"]["set"] is False
        assert c.post("/api/setup/api-key/check").status_code == 400
    assert not (tmp_path / ".env").exists()


class RejectingExtractor:
    def extract(self, request: ExtractionRequest) -> Any:
        raise AccountError("Anthropic rejected the API key (AuthenticationError)")


def test_import_stops_once_when_the_key_is_rejected(tmp_path, data_dir):
    with app_client(tmp_path, data_dir, lambda key: None, extractor=RejectingExtractor) as c:
        pdf = make_pdf(tmp_path / "upload.pdf").read_bytes()
        c.post(
            "/api/setup/pdf", content=pdf, headers={**HEADERS, "Content-Type": "application/pdf"}
        )
        job = wait_for_import(c)["import"]
    assert job["state"] == "stopped"
    assert "rejected the API key" in job["message"] and "kept" in job["message"]
    assert (job["created"], job["failed"]) == (1, 0)  # the web print came first and was kept
