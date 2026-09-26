# ADR-0003: Rules-based ingredient-line parser

- Status: Accepted
- Date: 2026-09-26
- Requirements: REC-2, REC-6, NFR-1; M1 acceptance (95%+ of lines parse with no edits)

## Context

The stack offers `ingredient-parser-nlp` (a CRF model) or a rules parser with an agent fallback.
The collection's lines are mostly from recipe-plugin web prints and are regular: quantity,
unit, name, comma, prep note. The hard cases are specific and recurring: unicode fractions,
ranges, package sizes ("2 (15-ounce) cans"), "plus" amounts, UK "1 x 400g tin", decilitres in
the Icelandic booklet.

## Decision

A deterministic rules parser (`core/parser.py`) driven by a golden file of real-shaped lines
(`tests/golden/ingredient_lines.json`), fuzzed with hypothesis. Name matching is a separate
step against the ingredient catalog (`core/normalizer.py`), which records how each match was
made. Lines it cannot read keep an empty quantity or ingredient and show up as review issues;
nothing is guessed.

## Consequences

- Fully deterministic and fast; every behaviour is pinned by a golden case.
- New line shapes need a new golden case and a rule. If the unmatched rate on the real
  collection stays above 5%, revisit `ingredient-parser-nlp` behind the same function.
