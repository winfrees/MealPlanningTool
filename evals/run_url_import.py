"""M5 acceptance for URL import (ING-2): fetch and parse each link in evals/urls.txt with no agent.

Network only, no API key. Reports per link: parsed or why not, title, ingredient and step
counts, times and servings found. Exit code 1 if fewer than all links parse.
"""

import sys
from pathlib import Path

from mealplan.ingest.fetch import FetchError, fetch_page
from mealplan.ingest.url import parse_recipes_html

URLS = Path(__file__).parent / "urls.txt"


def main(argv: list[str]) -> int:
    lines = argv or URLS.read_text(encoding="utf-8").splitlines()
    urls = [u.strip() for u in lines if u.strip() and not u.strip().startswith("#")]
    if not urls:
        print(f"No links yet: add recipe URLs to {URLS} (one per line) or pass them as arguments.")
        return 0
    ok = 0
    for url in urls:
        try:
            recipes = [r for r in parse_recipes_html(fetch_page(url).html) if r.ingredients]
        except FetchError as e:
            print(f"FAIL  {url}\n      {e}")
            continue
        if not recipes:
            print(f"FAIL  {url}\n      no schema.org Recipe data on the page")
            continue
        r = recipes[0]
        ok += 1
        times = "/".join(str(t) if t else "-" for t in (r.prep_minutes, r.cook_minutes))
        print(
            f"ok    {url}\n      {r.title}: {len(r.ingredients)} ingredients, "
            f"{len(r.steps)} steps, serves {r.servings or '?'}, prep/cook {times} min"
        )
    print(f"\n{ok} of {len(urls)} imported with no agent")
    return 0 if ok == len(urls) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
