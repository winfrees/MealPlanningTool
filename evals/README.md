# Agent evals

One fixed test set per agent, with expected outputs, run on every prompt or model change with
`make evals`. Evals call the real API and cost money; unit tests never do.

| Agent | Set | Runner | Milestone |
| --- | --- | --- | --- |
| PDF recipe extractor | 10 PDF pages (`extractor/cases/`) | `run_extractor.py` | M1 |
| Web recipe scout | 4 requests with household rules (`scout/cases/`) | `run_scout.py` | M5 |
| URL import (no agent) | the links you list in `urls.txt` | `run_url_import.py` (`make url-evals`) | M5 |
| Kitchen vision inventory | 10 kitchen photos | | M6 |

The extractor eval needs `data/source/Recipes_12Sept26.pdf` and an API key
(`MEALPLAN_ANTHROPIC_API_KEY` in `.env`, `ANTHROPIC_API_KEY`, or an `ant auth login` profile).

The scout eval also fetches real pages. The URL import eval needs no key at all: it is the M5
acceptance check that 20 links import with no agent, so put 20 links in `urls.txt` first.
