"""Eval for the PDF recipe extractor: runs each case against the real API and scores it.

Each case in evals/extractor/cases/*.json names a manifest id and the expected ingredient
lines (checked by hand against the PDF). Scores: ingredient recall and precision after
normalization, and the step count. Costs real money; run on prompt or model changes.
"""

import json
import sys
from pathlib import Path

from mealplan.agents.extractor import ClaudeExtractor, ExtractionRequest
from mealplan.config import get_settings
from mealplan.ingest.grounding import normalize
from mealplan.ingest.pdf import IMAGE_FORMATS, load_pages
from mealplan.models.manifest import load_manifest

CASES = Path(__file__).parent / "extractor" / "cases"


def main() -> int:
    settings = get_settings()
    pdf = settings.data_dir / "source" / "Recipes_12Sept26.pdf"
    cases = sorted(CASES.glob("*.json"))
    if not cases or not pdf.exists():
        print(f"Skipping extractor evals: {len(cases)} cases, PDF present: {pdf.exists()}")
        return 0

    manifest = {
        r.id: r for r in load_manifest(settings.data_dir / "core_recipe_manifest.json").recipes
    }
    extractor = ClaudeExtractor()
    total_cost = 0.0
    rows = []
    for path in cases:
        case = json.loads(path.read_text(encoding="utf-8"))
        entry = manifest[case["ref"]]
        pages = load_pages(pdf, entry.page_list, entry.format in IMAGE_FORMATS)
        result = extractor.extract(ExtractionRequest(pages, target_title=entry.title))
        total_cost += sum(c.cost_usd for c in result.calls)
        if result.failure or not result.recipes:
            rows.append(
                (
                    case["ref"],
                    0.0,
                    0.0,
                    "FAILED",
                    result.failure.stage if result.failure else "empty",
                )
            )
            continue
        got = {normalize(x) for x in result.recipes[0].ingredients}
        want = {normalize(x) for x in case["ingredients"]}
        recall = len(got & want) / len(want) if want else 1.0
        precision = len(got & want) / len(got) if got else 0.0
        steps = f"{len(result.recipes[0].steps)}/{case.get('steps', '?')}"
        rows.append((case["ref"], recall, precision, steps, "ok"))

    print(f"{'ref':10} {'recall':>7} {'precision':>9} {'steps':>7}  outcome")
    for ref, recall, precision, steps, outcome in rows:
        print(f"{ref:10} {recall:7.2f} {precision:9.2f} {steps:>7}  {outcome}")
    mean = sum(r[1] for r in rows) / len(rows)
    print(f"mean recall {mean:.2f}; cost ${total_cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
