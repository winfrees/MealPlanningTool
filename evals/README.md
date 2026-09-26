# Agent evals

One fixed test set per agent, with expected outputs, run on every prompt or model change with
`make evals`. Evals call the real API and cost money; unit tests never do.

| Agent | Set | Runner | Milestone |
| --- | --- | --- | --- |
| PDF recipe extractor | 10 PDF pages (`extractor/cases/`) | `run_extractor.py` | M1 |
| Web recipe scout / URL import | 10 URLs | | M4 |
| Kitchen vision inventory | 10 kitchen photos | | M5 |

The extractor eval needs `data/source/Recipes_12Sept26.pdf` and an API key
(`MEALPLAN_ANTHROPIC_API_KEY` in `.env`, `ANTHROPIC_API_KEY`, or an `ant auth login` profile).
