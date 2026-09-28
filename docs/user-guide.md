# The weekly routine

## The web app

```sh
uv run mealctl serve                 # opens http://localhost:8000 in your browser
uv run mealctl serve --demo          # look around with sample recipes first (password: demo)
uv run mealctl serve --host 0.0.0.0  # reachable from phones on the home network
```

The first run asks you to choose a household password (it is kept in `.env`). A new install
opens on **Get started**: import `Recipes_12Sept26.pdf` from the browser, approve the recipes
that look fine, and plan the week. Web-print pages import for free; scanned and photographed
pages need Claude: paste an API key from console.anthropic.com (it starts with `sk-ant-`) into
the **Claude** box on Get started (or **Setup** later), which checks it and saves it in `.env`,
then import again (recipes already imported are skipped). In `.env` the key can be written as
`ANTHROPIC_API_KEY=...` or `MEALPLAN_ANTHROPIC_API_KEY=...`; no model name is needed. The
demo uses its own `demo.db` and never touches your recipes.

### A local model instead (Ollama + Docling)

Scanned pages can also be read on this computer, free and private:

1. Install Ollama from ollama.com and pull a model: `ollama pull qwen2.5:7b` (about 5 GB; any
   model that follows JSON well works, set `MEALPLAN_OLLAMA_MODEL=...` in `.env`).
2. Install Docling, which does the OCR and page layout: `uv sync --extra local` (large: it
   brings PyTorch). The first import downloads its OCR and layout models once.
3. Check it: `uv run mealctl local`, or **Check local model** on the Setup page.
4. Choose **Local model (Ollama)** as the reader on Setup (or leave it on **Automatic**, which
   uses the local model when no Claude key is saved), then import.

Each scanned page is read by Docling, then the model writes the recipe as JSON, and every
ingredient line is checked against what Docling read, with one retry, just like Claude. On a
CPU expect a minute or more per recipe; the import runs in the background with progress.
From the terminal: `mealctl import pdf data/source/Recipes_12Sept26.pdf --engine local`.
For a vision model (for example `qwen2.5vl:7b`) set `MEALPLAN_OLLAMA_VISION=true` so the
page images are sent too.

### No API key: import with a Claude chat

Under **Use a Claude chat instead** (Get started, or **Setup** later) the recipes still missing
are split into batches of about ten pages. For each batch:

1. **Download PDF** (just that batch's pages) and **Copy prompt**.
2. In a new chat at claude.ai, attach the PDF, paste the prompt and send.
3. Copy Claude's whole reply, paste it into **Claude's reply**, and press **Check reply**. The
   check lists each recipe it found and anything wrong (an unknown id, no ingredients).
4. **Import**. The recipes land in **Review** as drafts tagged `claude-chat`, with the page
   they came from. Batches you have finished drop off the list.

From the terminal: `mealctl import chat-batches data/source/Recipes_12Sept26.pdf` writes the
batch PDFs and prompts to `chat-batches/`; save each reply to a file and run
`mealctl import chat reply.txt` (add `--dry-run` to check first).

### Fixing a recipe

**Edit** (on a Review card or a recipe page) opens the editor: title, servings, meal, times,
ingredients (one per line, as written), steps with hands-on and waiting minutes and equipment,
tags and notes. Saving matches the ingredients to the catalog again and lists anything still
to check, such as an ingredient it does not recognise. Hands-on minutes matter: the planner
uses them for the weeknight limit and the Sunday prep schedule.

Everything below also works in the browser: **Week** (plan, shuffle, swap, lock, cooked, day
cards, prep checklist), **Recipes** (search, open, scale, rate, tag), **Review** (approve,
reject, merge copies, group variants), **Shop** (tick items off as you go; text and PDF
downloads) and **Kitchen** (inventory and the staples check). On a home server, put it
behind a TLS reverse proxy before exposing it beyond the home network.

### Finding new recipes (Discover)

The **Discover** tab finds recipes on the web and checks each one before you see it:

- **Presets**: *Soups for Sunday prep*, *Grain salads & lunch bowls*, *Weeknight dinners*
  (within your hands-on limit), or type what you want. With an API key saved, Claude searches
  the web (a minute or two, a few cents); without one, you get a prompt to paste into a claude.ai
  chat, then paste its reply back.
- **Add from a link**: paste one or more recipe links; no AI is involved.

Either way the app reads each page itself and labels it: **fits**, **looks familiar** (similar
to one of yours: add it as another version in that recipe's family, or as its own recipe),
**breaks a rule** (an ingredient you avoid, or too spicy, with the reason), **already yours**,
or **couldn't read** (the site doesn't publish recipe data). Added recipes wait in **Review**,
marked as new finds. The planner uses at most one new dinner a week; rate one 4 or 5 after
cooking, or press *move it to your recipes*, and it becomes one of your own.

From the terminal: `mealctl import url LINK...`, `mealctl discover run --preset soups [--add]`
(needs a key), `mealctl discover prompt --preset lunches` and `mealctl discover chat reply.txt`,
`mealctl recipes promote REF`.

## From the terminal

Friday to Sunday. Dates default to the coming prep day (Sunday).

```sh
uv run mealctl plan week            # Friday: draft the week (dinners, lunches, conflicts)
uv run mealctl plan swap 2026-10-06 dinner core-048   # change anything you don't want
uv run mealctl plan lock            # lock it once it looks right
uv run mealctl list show --format text   # Saturday: the shopping list, net of what you have
uv run mealctl prep show            # Sunday: the prep checklist, in the order to start things
uv run mealctl plan cards           # the week's day cards: tonight's dinner, tomorrow's lunch
```

After prep: `uv run mealctl prep done`. After a meal: `uv run mealctl plan cooked 2026-10-07`
(takes what it used out of inventory) and `uv run mealctl rate core-044 5 --repeat`.

## Inventory

```sh
uv run mealctl inventory add "ground beef" 2 lb --location freezer
uv run mealctl inventory add eggs 12            # counts need no unit
uv run mealctl inventory list                   # by location, soonest best-by first
uv run mealctl inventory expiring               # use-first list (also steers the planner)
uv run mealctl inventory staples --out "olive oil,cumin"   # staples check; --ok if nothing is out
```

Best-by dates default from the ingredient's shelf life (90 days in the freezer). The list can be
saved for a phone (`--format text --output list.txt`) or printed (`--format pdf --output list.pdf`).

Every week starts from the base week: Monday salmon, Tuesday tacos (beans and meat in turn),
Friday pizza, and planner-chosen dinners on the other days. See or change it with
`mealctl plan base`, for example `mealctl plan base thu core-044` for a standing Thursday,
`mealctl plan base tue house-002,house-003` for a rotation, or `mealctl plan base thu menu`.
The standing meals are ordinary recipes (`house-001` to `house-004`); edit them like any other.

Household rules live in `mealctl prefs show`; change one with, for example,
`mealctl prefs set dinner_servings 4` or `mealctl prefs set avoid_ingredients '["shrimp"]'`.
Tag a recipe `mild` if the spice estimate is wrong, `very-spicy` to exclude it,
`weeknight-friendly` if its time is unknown but quick, and `no-leftovers` if it does not reheat.

