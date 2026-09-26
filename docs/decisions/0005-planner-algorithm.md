# ADR-0005: Planner algorithm

- Status: Accepted
- Date: 2026-09-26
- Requirements: PLN-1, PLN-2, PLN-3, PLN-4, PLN-5, PLN-7, PLN-8, REC-9, NFR-1, NFR-3

## Context

The stack suggests "scored greedy search with seed; OR-Tools CP-SAT if constraints grow". A week
has 7 dinners, 5 lunches and one prep session; the library holds about 50 dinners. Plans must be
reproducible (same inputs and seed give the same plan) and explainable when a rule cannot be met.

## Decision

- `core/planner.py` is a pure function over plain snapshots (`Dish`, `ComponentSpec`,
  history, expiring items, locked slots). The database is read and written only by
  `core/plan_store.py`, so golden tests run the planner without a database.
- **Hard rules** filter candidates: avoid tags and ingredients, spice cap, weeknight hands-on
  limit, one dish per variant family (its preferred variant), at most one discovered dinner,
  no repeat within 14 days, at most two dinners per protein. When a day has no candidate the
  repeat window, then the protein cap, is relaxed and the relaxation is reported; household
  rules (avoid, spice, weeknight time) are never relaxed.
- **Score**: favorites (unrated counts as average), season fit, expiring inventory, novelty,
  and a leftover fit on nights before the late-week lunch days, plus jitter seeded per recipe
  (`Random(f"{seed}:{ref}")`) that only breaks near-ties.
- Weeknights choose first (tightest rules). Leftovers fill lunch days from Friday backwards;
  template lunches fill the start of the week, when Sunday-prepped food is freshest.
- Prep is list scheduling on one cook and limited equipment (`core/prep.py`).
- Saved meal slots are the record of a week. Swaps pin the other dinners and recompute
  leftovers, lunches and prep around the change.

## Consequences

- Deterministic and fast (a 150-recipe week plans in well under the 2 s budget).
- Greedy can miss a globally better week; the rule tests and hypothesis checks guard the hard
  rules for every seed. Move to CP-SAT if rules start to interact (multi-week balance, budgets).
- The scoring weights are guesses to tune from real weeks and ratings.
