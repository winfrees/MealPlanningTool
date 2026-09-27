"""Web API (UI-4..UI-7): auth, CSRF, and each screen's endpoints over the golden library."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from mealplan import db
from mealplan.config import Settings
from mealplan.core.normalizer import Catalog, seed_catalog
from mealplan.web import auth
from mealplan.web.app import create_app
from tests.planning_fixtures import GOLDEN_WEEK, populate_golden

TODAY = date(2026, 10, 3)  # Saturday: "this week" starts Sun 4 Oct, the golden week
HEADERS = {auth.CSRF_HEADER: "1"}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "web.db",
        data_dir=Path("data"),
        web_password=SecretStr("correct horse"),
    )


@pytest.fixture
def client(tmp_path: Path, catalog: Catalog) -> Iterator[TestClient]:
    settings = make_settings(tmp_path)
    app = create_app(settings, today=lambda: TODAY)
    with db.session_scope(db.make_engine(settings.db_url)) as s:
        seed_catalog(s, catalog)
        populate_golden(s, catalog)
    with TestClient(app, headers=HEADERS) as c:
        assert c.post("/api/login", json={"password": "correct horse"}).status_code == 200
        yield c


def test_refuses_to_start_without_a_password(tmp_path):
    with pytest.raises(RuntimeError, match="MEALPLAN_WEB_PASSWORD"):
        create_app(Settings(db_path=tmp_path / "x.db"))


def test_everything_needs_a_login(tmp_path, catalog):
    app = create_app(make_settings(tmp_path), today=lambda: TODAY)
    with TestClient(app, headers=HEADERS) as c:
        assert c.get("/api/session").json() == {"authenticated": False}
        for path in ("/api/week", "/api/recipes", "/api/review", "/api/list", "/api/inventory"):
            assert c.get(path).status_code == 401, path
        assert c.post("/api/week/plan", json={}).status_code == 401
        assert c.post("/api/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/login", json={"password": "correct horse"}).status_code == 200
        assert c.get("/api/session").json() == {"authenticated": True}
        cookie = c.cookies.get(auth.COOKIE)
        assert cookie and auth.token_ok(auth.load_secret(tmp_path / "web.secret"), cookie)
        c.post("/api/logout")
        assert c.get("/api/recipes").status_code == 401


def test_session_cookie_flags(tmp_path):
    app = create_app(make_settings(tmp_path), today=lambda: TODAY)
    with TestClient(app, headers=HEADERS) as c:
        response = c.post("/api/login", json={"password": "correct horse"})
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie


def test_writes_need_the_csrf_header(client):
    response = client.post("/api/week/plan", json={}, headers={auth.CSRF_HEADER: ""})
    assert response.status_code == 403
    assert (
        client.post(
            "/api/login", json={"password": "x"}, headers={auth.CSRF_HEADER: ""}
        ).status_code
        == 403
    )


def test_login_is_rate_limited(tmp_path):
    app = create_app(make_settings(tmp_path), today=lambda: TODAY)
    with TestClient(app, headers=HEADERS) as c:
        for _ in range(auth.MAX_FAILURES):
            assert c.post("/api/login", json={"password": "guess"}).status_code == 401
        assert c.post("/api/login", json={"password": "correct horse"}).status_code == 429


def test_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200 and "<script" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_token_expiry_and_tampering():
    secret = b"k" * 32
    token = auth.make_token(secret, now=1000, lifetime=60)
    assert auth.token_ok(secret, token, now=1059)
    assert not auth.token_ok(secret, token, now=1061)
    assert not auth.token_ok(secret, token.replace(".", ".0"), now=1000)
    assert not auth.token_ok(b"x" * 32, token, now=1000)
    assert not auth.token_ok(secret, "garbage", now=1000)
    assert not auth.token_ok(secret, None)


def test_week_plan_screen(client):
    assert client.get("/api/calendar").json() == {"today": "2026-10-03", "week_start": "2026-10-04"}
    assert client.get("/api/week").json() is None  # not planned yet
    week = client.post("/api/week/plan", json={"seed": 7}).json()
    assert week["week_start"] == "2026-10-04" and week["status"] == "draft"
    days = {d["date"]: d for d in week["days"]}
    assert days["2026-10-05"]["dinner"]["ref"] == "house-001"
    assert days["2026-10-09"]["dinner"]["title"] == "Pizza Night (order in)"
    assert days["2026-10-07"]["lunch"]["leftover_of"] == "2026-10-06"
    assert any("Order in" in line for line in days["2026-10-09"]["card"])
    assert week["prep"]["tasks"][0]["name"].startswith("Italian Wedding Soup")
    assert client.get("/api/week").json() == week

    swapped = client.post(
        "/api/week/swap", json={"day": "2026-10-08", "meal": "dinner", "ref": "core-048"}
    ).json()
    thursday = next(d for d in swapped["days"] if d["date"] == "2026-10-08")
    assert thursday["dinner"]["ref"] == "core-048" and thursday["dinner"]["locked"]

    locked = client.post("/api/week/lock", json={"locked": True}).json()
    assert locked["status"] == "locked"
    response = client.post("/api/week/plan", json={})
    assert response.status_code == 400 and "locked" in response.json()["detail"]

    cooked = client.post("/api/week/cooked", json={"day": "2026-10-06"}).json()
    assert "tortilla (18 each)" in cooked["not_in_inventory"]
    assert client.post("/api/prep/done", json={}).status_code == 200

    base = {b["day"]: b["meals"] for b in client.get("/api/base-week").json()}
    assert [m["ref"] for m in base["tue"]] == ["house-002", "house-003"] and base["wed"] == []


def test_recipe_search_and_detail(client):
    everything = client.get("/api/recipes").json()
    assert {"core-024", "house-001"} <= {r["ref"] for r in everything}
    soups = client.get("/api/recipes", params={"role": "soup"}).json()
    assert {r["ref"] for r in soups} == {"core-034", "core-038", "core-058"}
    assert [r["ref"] for r in client.get("/api/recipes", params={"q": "berbere"}).json()] == [
        "core-001"
    ]
    tacos = client.get("/api/recipes", params={"family": "tacos"}).json()
    assert {r["ref"] for r in tacos} == {"house-002", "house-003"}
    top = client.get("/api/recipes", params={"min_rating": 5}).json()
    assert {r["ref"] for r in top} == {"core-001", "core-023", "core-044"}

    detail = client.get("/api/recipes/core-024", params={"servings": 2}).json()
    assert detail["scaled"] and detail["shown_servings"] == 2
    eggs = next(i for i in detail["ingredients"] if i["name"] == "egg")
    assert (eggs["qty"], eggs["unit"]) == ("3", None)
    assert detail["variants"] == ["core-040"]
    assert client.get("/api/recipes/core-999").status_code == 400

    assert (
        client.post("/api/recipes/core-024/rate", json={"score": 5, "repeat": True}).status_code
        == 200
    )
    assert client.post("/api/recipes/core-024/rate", json={"score": 9}).status_code == 422
    tagged = client.post("/api/recipes/core-001/tags", json={"add": ["mild"]}).json()
    assert "mild" in tagged["tags"]


def test_review_queue(client, catalog, tmp_path):
    from mealplan.core import library
    from mealplan.models.enums import SourceKind
    from mealplan.models.schemas import IngredientLine, RecipeDraft, SourceRef

    settings = make_settings(tmp_path)
    with db.session_scope(db.make_engine(settings.db_url)) as s:
        for title in ("Easy Shakshuka", "Pumpkin Soup", "Mystery Stew"):
            draft = RecipeDraft(
                title=title,
                ingredients=[IngredientLine(raw_text="2 cups unobtainium")],
                sources=[SourceRef(kind=SourceKind.MANUAL)],
            )
            library.create_draft(s, draft, catalog)
    queue = {r["title"]: r for r in client.get("/api/review").json()}
    assert set(queue) == {"Easy Shakshuka", "Pumpkin Soup", "Mystery Stew"}
    copy = queue["Easy Shakshuka"]
    assert "unmatched ingredient: '2 cups unobtainium'" in copy["issues"]
    assert copy["similar"][0]["ref"] == "core-024"

    merged = client.post(f"/api/review/{copy['ref']}/merge", json={"into": "core-024"}).json()
    assert merged == {"ref": "core-024", "sources": 2}
    soup = queue["Pumpkin Soup"]["ref"]
    assert client.post(f"/api/review/{soup}/family", json={"name": "soups"}).status_code == 200
    assert client.post(f"/api/review/{soup}/approve").json()["status"] == "approved"
    assert client.post(f"/api/review/{queue['Mystery Stew']['ref']}/reject").status_code == 200
    assert client.get("/api/review").json() == []
    assert client.post(f"/api/review/{soup}/approve").status_code == 400


def test_shopping_list_and_inventory(client):
    client.post("/api/week/plan", json={"seed": 7})
    listing = client.get("/api/list").json()
    texts = [ln["text"] for ln in listing["lines"]]
    golden = (GOLDEN_WEEK / "shopping.md").read_text(encoding="utf-8")
    assert all(f"- [ ] {t}" in golden for t in texts)
    assert listing["staples"] == ["kosher salt", "olive oil"]

    text = client.get("/api/list/export", params={"format": "text"})
    assert text.headers["content-disposition"].endswith('shopping-2026-10-04.txt"')
    assert "PRODUCE" in text.text
    pdf = client.get("/api/list/export", params={"format": "pdf"})
    assert pdf.content.startswith(b"%PDF")
    assert client.get("/api/list/export", params={"format": "doc"}).status_code == 400

    added = client.post(
        "/api/inventory",
        json={"name": "ground beef", "qty": 3, "unit": "lb", "location": "freezer"},
    ).json()
    assert client.post("/api/inventory", json={"name": "unobtainium", "qty": 1}).status_code == 400
    stock = client.get("/api/inventory").json()
    beef = next(i for i in stock["items"] if i["id"] == added["id"])
    assert (beef["name"], beef["qty_text"], beef["location"]) == ("ground beef", "3", "freezer")
    assert any(i["name"] == "spinach" and i["expiring"] for i in stock["items"])  # best by Oct 6
    assert "ground beef" in {ln["name"] for ln in client.get("/api/list").json()["have"]}

    assert client.patch(f"/api/inventory/{added['id']}", json={"qty": 1}).status_code == 200
    assert client.delete(f"/api/inventory/{added['id']}").status_code == 200
    assert client.delete(f"/api/inventory/{added['id']}").status_code == 400

    staples = client.post("/api/staples", json={"out": ["olive oil"]}).json()
    assert staples == {"out": ["olive oil"], "check_due": False}
    assert "olive oil" in client.get("/api/catalog").json()


def test_preferences(client):
    assert client.get("/api/prefs").json()["dinner_servings"] == 4
    updated = client.post("/api/prefs", json={"key": "dinner_servings", "value": "3"}).json()
    assert updated["dinner_servings"] == 3
    assert client.post("/api/prefs", json={"key": "max_spice", "value": "9"}).status_code == 400
