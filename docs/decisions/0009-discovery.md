# ADR-0009: Discovery — agents propose links, the core fetches and checks them

- Status: Accepted
- Date: 2026-09-28
- Requirements: ING-2, ING-3, REC-7, REC-8, PLN-8, NFR-7, §5 guardrails

## Context

M5 adds recipes from the web. The library is dinner-heavy, so the first need is Sunday-prep
soups and lunches. The household may or may not have an API key. The spec's guardrails apply:
agents never write, dislikes are checked deterministically (an agent's claims are not trusted),
near-duplicates of core are variants rather than new dishes, and every find is reviewed.

## Decision

- **One pipeline for every source.** A pasted link, the scout agent and a claude.ai chat reply
  all produce only URLs (plus the suggester's one-line reason). The core fetches each page
  itself, parses its schema.org Recipe JSON-LD, and runs `discovery.check_page`: already in the
  library, unreadable, blocked (with reasons), duplicate (with the look-alikes), or ok. Adding
  re-fetches and re-checks server-side; the browser never supplies recipe content.
- **Own JSON-LD parser, no scraper dependency.** `ingest/url.py` handles top-level objects,
  lists, `@graph`, `mainEntity`, `@type` lists, HowToStep/HowToSection/string instructions, ISO
  8601 durations and yields in words. Pages without structured data are reported, not guessed;
  the scout is told to prefer sites that publish it. Golden pages pin the parser (NFR-1).
- **One dislike rule.** `recipe_facts.violations` is the planner's hard filter (avoided tags and
  ingredients, spice) with reasons, now shared by planner and discovery; unmatched raw lines are
  also searched for avoided foods.
- **Scout on the Messages API.** `web_search_20260209` (social/video sites blocked, 8 searches)
  plus one read-only client tool, `search_library`; structured output for
  `{suggestions: [{url, title, reason}]}`; a manual loop so each call is logged (tokens plus
  $0.01 per search) and budgeted like the extractor, with `pause_turn` resumed and one retry.
- **No key: a chat prompt.** The same brief becomes a prompt for claude.ai; links are read from
  the pasted reply's JSON block, or any links in the text, and checked the same way.
- **Safe fetching.** http(s) only; every host and redirect must resolve to public addresses
  (the web app must not become a way into the home network); 10 s, 3 MB, 5 redirects, HTML only.
- **Where finds go.** Discovered drafts in the review queue (ING-3); a duplicate can be added as
  a variant in the look-alike's family (REC-8); the planner already caps discovered dinners at
  one a week (PLN-8); a 4+ rating or "move to my recipes" promotes to core (REC-7).

## Consequences

- The scout cannot put anything unchecked in front of the household, and its quality is
  measurable (`evals/run_scout.py`: how many suggestions fit).
- Sites without JSON-LD cannot be imported until an agent fallback for page text is added.
- DNS rebinding between the check and the request is not defended against; acceptable for a
  household tool on a home network.
