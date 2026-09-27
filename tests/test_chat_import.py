"""Importing recipes read by Claude in a chat (ING-1, ING-3): batches, prompt, reply parsing."""

import json
from collections.abc import Iterator, Mapping
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from mealplan.core import library
from mealplan.ingest import chat_import
from mealplan.ingest.chat_import import ChatImportError, batches, import_reply, parse_reply, preview
from mealplan.models.enums import RecipeStatus, SourceKind
from mealplan.models.manifest import Manifest
from mealplan.models.tables import RecipeFamily
from tests.test_first_run import data_dir as data_dir  # fixture
from tests.test_first_run import new_install
from tests.test_pdf_import import MANIFEST, make_pdf


def entry(i: int, pages: str) -> dict[str, str]:
    return {
        "id": f"core-{i:03d}",
        "title": f"Recipe {i}",
        "source": "x",
        "pages": pages,
        "format": "scanned image",
        "meal_role": "dinner",
    }


def reply(*recipes: Mapping[str, object], fence: bool = True) -> str:
    body = json.dumps({"recipes": list(recipes)}, indent=2)
    return f"Here you go!\n\n```json\n{body}\n```\n\nLet me know." if fence else body


BRUSCHETTA: dict[str, object] = {
    "id": "core-049",
    "title": "Grilled Bruschetta Chicken",
    "servings": "Serves 4",
    "ingredients": ["2 lb chicken breasts", "1 cup cherry tomatoes, halved"],
    "steps": ["Grill the chicken.", {"text": "Top with tomatoes.", "active_minutes": 5}],
}


def test_batches_respect_page_and_recipe_limits():
    manifest = Manifest.model_validate(
        {
            "source_pdf": "x.pdf",
            "pages": 40,
            "recipes": [entry(i, f"{i}-{i + 4}" if i == 3 else str(i)) for i in range(1, 20)],
        }
    )
    groups = batches(manifest.recipes)
    assert all(len(b.entries) <= chat_import.MAX_BATCH_RECIPES for b in groups)
    assert all(len(b.pages) <= chat_import.MAX_BATCH_PAGES for b in groups)
    assert [e.id for b in groups for e in b.entries] == [e.id for e in manifest.recipes]
    assert [b.number for b in groups] == list(range(1, len(groups) + 1))


def test_batch_pdf_holds_just_the_batch_pages(tmp_path):
    pdf = make_pdf(tmp_path / "Recipes_12Sept26.pdf")
    batch = batches([MANIFEST.recipes[1], MANIFEST.recipes[2]])[0]
    data = chat_import.batch_pdf(pdf, batch)
    with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        assert doc.page_count == 2
        assert "Misir Wat" in doc[0].get_text()


def test_prompt_maps_ids_to_pages_of_the_attached_file():
    batch = batches([MANIFEST.recipes[1], MANIFEST.recipes[2]])[0]
    text = chat_import.prompt(batch)
    assert 'core-026: "Misir Wat (Lentil Stew)" (page 1)' in text
    assert 'core-049: "Grilled Bruschetta Chicken" (page 2)' in text
    assert "recipes-batch-01.pdf" in text and "```json" in text


@pytest.mark.parametrize("fence", [True, False])
def test_parse_reply_finds_the_json(fence):
    assert parse_reply(reply(BRUSCHETTA, fence=fence))[0]["id"] == "core-049"


def test_parse_reply_accepts_a_bare_recipe_or_list():
    assert len(parse_reply(json.dumps(BRUSCHETTA))) == 1
    assert len(parse_reply(json.dumps([BRUSCHETTA, BRUSCHETTA]))) == 2


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Sorry, I can't read that.", "no JSON"),
        ('```json\n{"recipes": [{"id": "core-049", "title": \n```', "cut off"),
        ('{"recipes": "none"}', "expected"),
    ],
)
def test_parse_reply_explains_problems(text, message):
    with pytest.raises(ChatImportError, match=message):
        parse_reply(text)


def test_preview_flags_unknown_duplicate_and_empty(session):
    items = preview(
        session,
        MANIFEST,
        reply(
            BRUSCHETTA,
            {**BRUSCHETTA, "title": "again"},
            {"id": "core-999", "title": "Mystery Stew", "ingredients": ["1 onion"]},
            {"id": "core-026", "title": "Misir Wat", "ingredients": [], "steps": []},
        ),
    )
    assert [(i.id, i.status) for i in items] == [
        ("core-049", "new"),
        ("core-049", "error"),
        ("core-999", "error"),
        ("core-026", "new"),
    ]
    assert items[0].ingredients == 2 and items[0].steps == 2
    assert "twice" in items[1].problems[0]
    assert "manifest" in items[2].problems[0]
    assert items[3].problems == ["no ingredients", "no steps"]


def test_preview_matches_by_title_when_the_id_is_missing(session):
    item = preview(session, MANIFEST, reply({**BRUSCHETTA, "id": ""}))[0]
    assert (item.id, item.status) == ("core-049", "new")


def test_import_creates_reviewable_drafts_with_provenance(session, catalog):
    items = import_reply(session, MANIFEST, catalog, reply(BRUSCHETTA))
    assert [(i.id, i.status) for i in items] == [("core-049", "created")]
    recipe = library.get_recipe(session, "core-049")
    assert recipe.status is RecipeStatus.DRAFT
    assert recipe.servings == 4
    assert [i.raw_text for i in recipe.ingredients] == BRUSCHETTA["ingredients"]
    assert [s.active_minutes for s in recipe.steps] == [0, 5]
    assert "claude-chat" in recipe.tags and "vision-extracted" in recipe.tags
    source = recipe.sources[0]
    assert (source.kind, source.file, source.pages) == (SourceKind.PDF, "Recipes_12Sept26.pdf", "3")
    assert source.confidence == chat_import.CHAT_CONFIDENCE
    # The manifest's household notes and seed rating come along, as with other imports.
    assert recipe.household_notes == "Handwritten: Excellent x2 (freezer)"
    assert library.mean_rating(session, recipe.id) is not None

    # Importing the same reply again leaves the draft alone (REC-8).
    again = import_reply(session, MANIFEST, catalog, reply(BRUSCHETTA))
    assert [i.status for i in again] == ["exists"]


def test_import_keeps_the_manifest_family(session, catalog):
    shakshuka = {
        "id": "core-024",
        "title": "Shakshuka",
        "ingredients": ["6 eggs"],
        "steps": ["Cook."],
    }
    import_reply(session, MANIFEST, catalog, reply(shakshuka))
    family = library.get_recipe(session, "core-024").family
    assert isinstance(family, RecipeFamily) and family.name == "shakshuka"


def test_missing_lists_what_the_library_lacks(session, catalog):
    assert [e.id for e in chat_import.missing(session, MANIFEST)] == [
        "core-024",
        "core-026",
        "core-049",
    ]
    import_reply(session, MANIFEST, catalog, reply(BRUSCHETTA))
    assert [e.id for e in chat_import.missing(session, MANIFEST)] == ["core-024", "core-026"]


# --- web API and CLI ---------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path: Path, data_dir: Path) -> Iterator[TestClient]:
    with new_install(tmp_path, data_dir) as c:
        yield c


def test_web_chat_import_needs_the_pdf_first(web):
    info = web.get("/api/chat-import").json()
    assert info["pdf_on_disk"] is False and info["missing"] == 3
    response = web.get("/api/chat-import/1/pdf")
    assert response.status_code == 404 and "upload" in response.json()["detail"]


def test_web_chat_import_round_trip(web, data_dir):
    (data_dir / "source").mkdir()
    make_pdf(data_dir / "source" / "Recipes_12Sept26.pdf")
    info = web.get("/api/chat-import").json()
    assert [b["number"] for b in info["batches"]] == [1]
    assert [r["id"] for r in info["batches"][0]["recipes"]] == ["core-024", "core-026", "core-049"]

    pdf = web.get("/api/chat-import/1/pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert 'filename="recipes-batch-01.pdf"' in pdf.headers["content-disposition"]
    assert "core-049" in web.get("/api/chat-import/1/prompt").json()["prompt"]

    preview_ = web.post("/api/chat-import/preview", json={"reply": reply(BRUSCHETTA)}).json()
    assert [(i["id"], i["status"]) for i in preview_] == [("core-049", "new")]
    assert web.get("/api/setup").json()["drafts"] == 0  # a preview writes nothing

    created = web.post("/api/chat-import", json={"reply": reply(BRUSCHETTA)}).json()
    assert [(i["id"], i["status"]) for i in created] == [("core-049", "created")]
    assert [r["ref"] for r in web.get("/api/review").json()] == ["core-049"]
    # The batch now lists only what is left.
    left = web.get("/api/chat-import").json()["batches"][0]["recipes"]
    assert [r["id"] for r in left] == ["core-024", "core-026"]


def test_web_chat_import_reports_bad_replies(web):
    response = web.post("/api/chat-import/preview", json={"reply": "Sorry, I can't."})
    assert response.status_code == 400 and "no JSON" in response.json()["detail"]
    assert web.get("/api/chat-import/99/prompt").status_code == 404


def test_web_edit_fixes_a_draft(web):
    web_reply = reply({**BRUSCHETTA, "ingredients": ["2 lb chicken breasts", "1 c. glorp"]})
    web.post("/api/chat-import", json={"reply": web_reply})
    form = web.get("/api/recipes/core-049/edit").json()
    assert form["status"] == "draft" and "dinner" in form["roles"]
    assert any("glorp" in i for i in form["issues"])
    assert form["steps"][1] == {
        "text": "Top with tomatoes.",
        "equipment": [],
        "active_minutes": 5,
        "passive_minutes": 0,
    }

    body = {
        "title": form["title"],
        "servings": 4,
        "meal_role": "dinner",
        "tags": form["tags"],
        "household_notes": form["household_notes"],
        "ingredients": ["2 lb chicken breasts", "1 cup cherry tomatoes, halved"],
        "steps": [{"text": "Grill the chicken.", "equipment": ["grill"], "active_minutes": 20}],
    }
    saved = web.put("/api/recipes/core-049", json=body)
    assert saved.status_code == 200 and saved.json()["issues"] == []
    assert web.get("/api/recipes/core-049").json()["steps"] == ["Grill the chicken."]
    assert web.put("/api/recipes/core-049", json={**body, "title": ""}).status_code == 422
    assert web.put("/api/recipes/nope", json=body).status_code == 400


def test_edit_needs_the_csrf_header(web):
    response = web.put("/api/recipes/x", json={"title": "x"}, headers={"X-Mealplan": ""})
    assert response.status_code == 403


def test_cli_chat_batches_and_import(tmp_path, monkeypatch, data_dir):
    from typer.testing import CliRunner

    from mealplan.cli import app

    monkeypatch.setenv("MEALPLAN_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("MEALPLAN_DATA_DIR", str(data_dir))
    runner = CliRunner()
    assert runner.invoke(app, ["catalog", "seed"]).exit_code == 0
    pdf = make_pdf(tmp_path / "Recipes_12Sept26.pdf")
    out = tmp_path / "batches"
    result = runner.invoke(app, ["import", "chat-batches", str(pdf), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == [
        "recipes-batch-01-prompt.txt",
        "recipes-batch-01.pdf",
    ]

    reply_file = tmp_path / "reply.txt"
    reply_file.write_text(reply(BRUSCHETTA))
    dry = runner.invoke(app, ["import", "chat", str(reply_file), "--dry-run"])
    assert dry.exit_code == 0 and "new" in dry.output and "Created" not in dry.output
    done = runner.invoke(app, ["import", "chat", str(reply_file)])
    assert done.exit_code == 0 and "Created 1 drafts" in done.output

    reply_file.write_text("no json here")
    assert runner.invoke(app, ["import", "chat", str(reply_file)]).exit_code == 1
