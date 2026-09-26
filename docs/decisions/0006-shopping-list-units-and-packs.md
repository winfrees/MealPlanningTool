# ADR-0006: Shopping list units, netting, and packs

- Status: Accepted
- Date: 2026-09-26
- Requirements: SHP-1, SHP-2, SHP-3, SHP-4, INV-1, INV-3, INV-4

## Context

Recipes mix units ("2 cups shredded cheese", "8 oz mozzarella", "2 (15-ounce) cans"), and
inventory is entered however it was bought. The list must add these up, net them against what
is on hand, and say what to buy, without guessing.

## Decision

- **One line per ingredient and unit.** Amounts convert to the catalog's `default_unit` when
  they can (using the catalog density for volume to weight); otherwise weights go to ounces and
  volumes to cups. Counts stay counts ("3 avocados", "2 cans"). Amounts that cannot convert to
  each other stay on separate lines, each noting the other.
- **Needs** are dinners scaled to planned servings (the leftover lunch is already inside the
  dinner), prep-day lunch batches (whole recipe when freezable, otherwise what is eaten), and
  make-ahead dinners once. Order-in nights add nothing.
- **Netting** converts every inventory item to the line's unit; items that cannot convert are
  noted on the line, never silently counted or dropped. The line keeps needed, have, and buy.
- **Packs**: package units (can, jar, bag...) are bought as counted; otherwise the amount is
  converted to the catalog pack unit and rounded up to whole packs. Liquids carry fluid-ounce
  pack sizes (broth cartons), solids net weight.
- **Staples** are assumed on hand and listed separately; a staple marked out is bought as one
  container. Unmatched ingredients and missing amounts stay on the list with a note.
- **Inventory items are always catalog ingredients**; a name the catalog cannot match is
  refused. Cooking a planned dinner or finishing prep deducts what it used, soonest best-by
  first, and reports what inventory did not cover.

## Consequences

- The list for the golden week is checked line by line against hand arithmetic
  (`tests/test_shopping.py`) and pinned byte-for-byte (`tests/golden/week/shopping.md`).
- Densities and pack sizes in `data/ingredients.csv` decide conversions; wrong or missing
  values show up as odd amounts or missing pack counts and are fixed in the CSV.
