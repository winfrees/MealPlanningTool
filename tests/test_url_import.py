"""ING-2 URL import with no agent: schema.org Recipe JSON-LD, golden pages (NFR-1)."""

import ipaddress
import json
import os
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from mealplan.ingest.fetch import MAX_BYTES, FetchError, check_url, fetch_page
from mealplan.ingest.url import parse_iso_duration, parse_recipes_html, parse_yield, to_draft
from mealplan.models.enums import Collection, MealRole, SourceKind

GOLDEN = Path(__file__).parent / "golden" / "url"
PAGES = sorted(p.stem for p in (GOLDEN / "pages").glob("*.html"))


@pytest.mark.parametrize("name", PAGES)
def test_golden_page(name):
    """Each saved page parses to the hand-written expectation, byte for byte.

    After an intended change: `UPDATE_GOLDEN=1 uv run pytest tests/test_url_import.py`, then
    review the diff."""
    html = (GOLDEN / "pages" / f"{name}.html").read_text(encoding="utf-8")
    got = (
        json.dumps([asdict(r) for r in parse_recipes_html(html)], indent=2, ensure_ascii=False)
        + "\n"
    )
    path = GOLDEN / "expected" / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(got, encoding="utf-8")
    assert got == path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("PT10M", 10),
        ("PT1H15M", 75),
        ("P0DT1H30M", 90),
        ("PT2H", 120),
        ("PT90S", 2),
        ("P1D", 1440),
        ("pt45m", 45),
        ("PT0M", None),
        ("", None),
        ("20 minutes", None),
        (None, None),
    ],
)
def test_parse_iso_duration(text, minutes):
    assert parse_iso_duration(text) == minutes


@pytest.mark.parametrize(
    ("value", "servings"),
    [
        (4, 4.0),
        ("4", 4.0),
        (["6", "6 servings"], 6.0),
        ("Makes 12 muffins", 12.0),
        ("4 to 6 servings", 4.0),
        ("Serves 2-3", 2.0),
        ("1 cup", None),
        ("2 loaves", 2.0),
        ("12 oz", None),
        ("", None),
        (0, None),
        (None, None),
    ],
)
def test_parse_yield(value, servings):
    assert parse_yield(value) == servings


# --- fetching (SSRF limits) --------------------------------------------------------------------

PUBLIC = {"recipes.example": ["93.184.216.34"], "cdn.example": ["2606:2800:220:1::1"]}


def resolver(host: str) -> list[str]:
    return PUBLIC.get(host, ["10.0.0.5"])


def client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        return routes[str(request.url)]

    return httpx.Client(transport=httpx.MockTransport(handle))


PLAIN = (GOLDEN / "pages" / "plain.html").read_text(encoding="utf-8")


def test_fetch_follows_checked_redirects():
    http = client(
        {
            "https://recipes.example/soup": httpx.Response(
                301, headers={"location": "https://cdn.example/soup-2"}
            ),
            "https://cdn.example/soup-2": httpx.Response(
                200, text=PLAIN, headers={"content-type": "text/html; charset=utf-8"}
            ),
        }
    )
    page = fetch_page("https://recipes.example/soup", http, resolver)
    assert page.final_url == "https://cdn.example/soup-2"
    assert parse_recipes_html(page.html)[0].title == "Red Lentil Soup"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://recipes.example/x",
        "http://localhost:8000/api/week",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://192.168.1.10/",
        "http://169.254.169.254/latest/meta-data",
        "http://printer.local/",
        "http://intranet.example/",  # resolves to 10.0.0.5 in this test
        "not a url",
    ],
)
def test_refuses_this_computer_and_the_home_network(url):
    def real_or_fake(host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            return resolver(host)

    with pytest.raises(FetchError):
        check_url(url, real_or_fake)


def test_a_redirect_into_the_home_network_is_refused():
    http = client(
        {
            "https://recipes.example/x": httpx.Response(
                302, headers={"location": "http://intranet.example/admin"}
            )
        }
    )
    with pytest.raises(FetchError, match="network"):
        fetch_page("https://recipes.example/x", http, resolver)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "404"),
        (
            httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
            "not a web page",
        ),
        (
            httpx.Response(
                200, content=b"x" * (MAX_BYTES + 10), headers={"content-type": "text/html"}
            ),
            "too large",
        ),
    ],
)
def test_fetch_problems_are_explained(response, message):
    http = client({"https://recipes.example/x": response})
    with pytest.raises(FetchError, match=message):
        fetch_page("https://recipes.example/x", http, resolver)


def test_redirect_loops_stop():
    loop = httpx.Response(302, headers={"location": "https://recipes.example/x"})
    with pytest.raises(FetchError, match="redirects"):
        fetch_page(
            "https://recipes.example/x", client({"https://recipes.example/x": loop}), resolver
        )


def test_url_draft_is_discovered_with_its_source():
    recipe = parse_recipes_html(PLAIN)[0]
    draft = to_draft(recipe, "https://recipes.example/soup")
    assert draft.collection is Collection.DISCOVERED and draft.meal_role is MealRole.SOUP
    source = draft.sources[0]
    assert (source.kind, str(source.url), source.title, source.author) == (
        SourceKind.URL,
        "https://recipes.example/soup",
        "Good Bowls",
        "Ana Silva",
    )
    assert [i.raw_text for i in draft.ingredients] == recipe.ingredients
    assert (
        to_draft(
            parse_recipes_html((GOLDEN / "pages" / "sections.html").read_text())[0],
            "https://x.example/c",
            role=MealRole.DINNER,
        ).meal_role
        is MealRole.DINNER
    )
