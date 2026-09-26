# ADR-0002: Retailer integration — Kroger API vs Instacart

- Status: Proposed. Blocked on two things: a check of current access and terms, and which stores the household uses.
- Date: 2026-09-26
- Requirements: RTL-1, RTL-2, RTL-3, RTL-4

## Context

M0 asks for a decision note on the first retailer adapter. Candidates from RTL-2:

| | Kroger public developer API | Instacart developer platform |
| --- | --- | --- |
| What it offers | Product search, store locator, add to a customer's cart (OAuth) | Shoppable list / recipe links the user opens in Instacart |
| Fits RTL-4 (never place an order) | Yes: fills the cart; person checks out | Yes: produces a link; person finishes |
| SKU memory (RTL-3) | Kroger product IDs per store | Instacart matches items itself; less to remember |
| Coverage | Kroger-family banners only | Many retailers, if they are on Instacart |

## To verify (manual, needs the household's accounts)

1. Which grocery stores does the household actually use? (open question in requirements §10)
2. Kroger: register an app at developer.kroger.com; confirm product search and cart scopes are
   still available to individual developers, and note rate limits and terms.
3. Instacart: confirm the developer platform still issues keys to individuals for shoppable list
   links, and note terms.

## Decision

Pending. Default if both are available: Kroger adapter first when the household shops at a
Kroger-family store (full cart fill and SKU memory); otherwise the Instacart link. The plain-text
and Markdown list export (SHP-5) is the fallback for either.

## Consequences

The `RetailerAdapter` protocol (`src/mealplan/retail/base.py`) is written now, so the choice does
not block M1–M5.
