# ADR-0007: Web app architecture

- Status: Accepted
- Date: 2026-09-26
- Requirements: UI-4, UI-5, UI-6, UI-7, NFR-4, NFR-5, NFR-9

## Context

The household wants a graphical interface for planning, searching and reviewing, lightweight
and JavaScript-based, cross-platform, and able to move to a home server later. The core is
Python and must stay the only place that writes.

## Decision

- **One process.** `mealctl serve` runs a FastAPI app (uvicorn) that serves a JSON API under
  `/api` and the frontend's static files. API handlers are thin: they call the same core
  functions as the CLI (`plan_store`, `kitchen`, `library`, `review_queue`, `inventory`), so
  every rule and test already in place applies to the web too.
- **No-build frontend.** Preact + htm as vendored ES modules (about 15 KB, MIT) in
  `web/static/vendor/`; plain JavaScript modules for each screen; one stylesheet, phone first.
  Nothing is fetched from a CDN, no Node is needed to run or host it, and it works offline.
- **Access.** One household password (`MEALPLAN_WEB_PASSWORD`); the server refuses to start
  without it. Login sets a signed, expiring, HttpOnly, SameSite=Strict cookie (HMAC-SHA256 with
  a secret generated per install and stored beside the database). Failed logins are
  rate-limited. State-changing requests must send an `X-Mealplan` header, which a cross-site
  form cannot (the PDF upload is the one non-JSON body). Binds to 127.0.0.1 unless `--host` says otherwise; for a home server,
  put it behind a TLS reverse proxy.
- **First run (UI-8).** The server prepares a new database itself (migrations, catalog, prep
  components, house meals) and, when no password is set and it runs in a terminal, asks for
  one and saves it to `.env` (mode 0600). A "Get started" guide replaces the empty week until
  the library has approved recipes. The PDF import runs on a background thread, one recipe per
  transaction, so the page can poll its progress and a stopped import keeps what it finished.
  `--demo` serves the sample library (`data/sample_library.json`, also the golden week's) from
  `demo.db`, rebuilt on every run, so trying the app never touches household data.
- **Tests.** API tests run in-process with the FastAPI test client; a Playwright browser test
  drives the real frontend end to end.

## Consequences

- The CLI and the web app cannot drift: both are thin layers over the same functions.
- No bundler, no transpiling: modern browsers only (ES modules), which covers every current
  desktop and phone browser.
- A single shared password suits one household; per-person accounts would need a user table.
