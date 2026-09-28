"""Eval for the web recipe scout (M5, §5). Calls the real API and fetches real pages.

Each case in evals/scout/cases/*.json is a preset or request plus household rules. The scout
runs against a fresh database holding the sample library, and every link goes through the
same checks as in the app. Scores per case: links suggested, how many fit, and how many were
blocked, look-alikes, unreadable or already known; the case passes when at least
`min_fitting` fit. The dislike guardrail itself is deterministic and unit-tested; this
measures how useful the scout's suggestions are.
"""

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from mealplan import db
from mealplan.agents.scout import ClaudeScout, presets, request_text
from mealplan.config import get_settings
from mealplan.core import discovery, setup
from mealplan.core.normalizer import Catalog
from mealplan.core.preferences import HouseholdPrefs
from mealplan.models.tables import AgentCall

CASES = Path(__file__).parent / "scout" / "cases"


def main() -> int:
    settings = get_settings()
    cases = sorted(CASES.glob("*.json"))
    scout = ClaudeScout()
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{Path(tmp) / 'eval.db'}"
        db.upgrade(url)
        engine = db.make_engine(url)
        with db.session_scope(engine) as s:
            setup.prepare(s, settings.data_dir)
            sample = setup.load_library_json(settings.data_dir / setup.SAMPLE_LIBRARY)
            setup.load_library(s, Catalog.from_db(s), sample["recipes"])
        for path in cases:
            case = json.loads(path.read_text(encoding="utf-8"))
            prefs = HouseholdPrefs(
                avoid_ingredients=case.get("avoid_ingredients", []),
                max_spice=case.get("max_spice", 2),
            )
            chosen = {p.key: p for p in presets(prefs)}.get(case.get("preset", ""))
            request = chosen.request if chosen else case["request"]
            with db.session_scope(engine) as s:
                before = s.scalar(select(func.coalesce(func.sum(AgentCall.cost_usd), 0.0)))
                found = discovery.scout_candidates(
                    s,
                    prefs,
                    Catalog.from_db(s),
                    scout,
                    request_text(request, prefs),
                    chosen.role if chosen else None,
                    weekly_budget_usd=1000.0,
                )
                cost = s.scalar(select(func.sum(AgentCall.cost_usd))) - before
            counts = {v: sum(c.verdict == v for c in found) for v in discovery.VERDICTS}
            passed = counts["ok"] >= case.get("min_fitting", 4)
            failures += not passed
            print(
                f"{'pass' if passed else 'FAIL'}  {path.stem}: {len(found)} links, "
                + ", ".join(f"{n} {v}" for v, n in counts.items())
                + f", ${cost:.2f}"
            )
            for c in found:
                print(f"      {c.verdict:10} {c.title or c.url}  {'; '.join(c.reasons)}")
    print(f"\n{len(cases) - failures} of {len(cases)} cases passed ({date.today()})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
