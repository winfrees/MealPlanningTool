# ADR-0004: PDF extraction agent

- Status: Accepted
- Date: 2026-09-26
- Requirements: ING-1, ING-3, NFR-5, NFR-7; guardrails in requirements §5

## Context

52 of the 93 core recipes cannot be read from the text layer alone (designed booklets, tall
web captures, notebook screenshots, a scanned page), so the extraction agent is needed in M1.

## Decision

- One Messages API call per recipe with the page text layers and, for image-bearing page
  types, page images. Model `claude-opus-5` with its default adaptive thinking.
- Output constrained by `output_config.format` (a JSON schema generated from Pydantic and made
  closed and self-contained), then validated with Pydantic. On a validation or grounding failure
  the error goes back to the model once; a second failure lands in `ingest_failure`.
- Ingredients come back as verbatim lines, not structured fields: the deterministic parser does
  the structure, and the grounding check can require each line to appear in the text layer.
  Image-only pages cannot be grounded; their drafts carry confidence 0.7 and the
  `vision-extracted` tag for the reviewer.
- Server-side refusal fallbacks are on (`fallbacks="default"`, beta
  `server-side-fallback-2026-07-01`). A refusal that survives them is not retried.
- Every call is logged to `agent_call` with model, tokens, latency, outcome, and cost; the import
  stops before the next call once the rolling 7-day spend reaches `MEALPLAN_AGENT_WEEKLY_BUDGET_USD`.
- The agent never writes to the database: `ingest/pdf.py` turns its output into drafts.

## Consequences

- Deterministic path first: web prints that parse cleanly cost nothing.
- Only prompts and page content go to the API (NFR-5).
- Prompt or model changes are checked with `make evals` (`evals/run_extractor.py`).
