# ADR-0008: Local extraction with Docling and Ollama

- Status: Accepted
- Date: 2026-09-27
- Requirements: ING-1, ING-7, NFR-5, NFR-7

## Context

About two thirds of the core collection is scans, phone screenshots and designed booklets
that the free web-print parser cannot read. Until now that meant the Claude API (a key and
per-call cost) or a manual Claude chat (ING-5). The household wants a third option that runs
entirely on their own computer.

## Decision

- **A second `RecipeExtractor`, not a new pipeline.** `OllamaExtractor` returns the same
  `ExtractionResult` and is checked by the same `check_output` (schema, then grounding), with
  the same single retry. Imports, drafts, review and the call log are unchanged.
- **Docling reads, the model structures.** Each page with an image is read by Docling (OCR and
  layout, as Markdown), which replaces that page's text layer. The model gets text (and, for
  a vision model, the images) and must answer in the output JSON schema, passed as Ollama's
  `format`, at temperature 0. Grounding checks ingredient lines against Docling's text, so a
  small model cannot invent ingredients unnoticed.
- **Plain HTTP to Ollama** (`/api/chat`, `/api/tags`) with httpx, no client library: two
  endpoints, easy to fake in tests.
- **Optional extra.** Docling brings PyTorch (gigabytes), so it is `uv sync --extra local`;
  without it the app works as before and says how to install it. Its models download on first
  use.
- **Choosing.** `MEALPLAN_EXTRACTOR` = auto (Claude if a key is set, else a ready local
  model), claude, local or none; the Setup page and `mealctl import pdf --engine` set it.
  Setup problems that would fail every page (Ollama down, model not pulled, Docling missing or
  unable to fetch its models) stop the import once with a clear message.
- **Default model** `qwen2.5:7b`: follows JSON schemas well and runs on a laptop CPU.

## Consequences

- Scans can be imported with no key, cost or data leaving the house; slower (a minute or
  more per recipe on a CPU) and likely less accurate than Claude, so drafts still go through
  review and the editor (ING-6).
- Tests use a mock transport and a fake page reader; a `local`-marked test runs real Docling
  OCR when the extra and its models are present (`make local-test`). This sandbox could not
  download Docling's models, so real OCR quality is unmeasured until the first household run.
