# Data

| File | What |
| --- | --- |
| `core_recipe_manifest.json` | Index of the 93 core recipes in `Recipes_12Sept26.pdf`: pages, source, format, meal role, variant family, household notes, copies. Also lists the non-recipe pages. Validated by `mealctl manifest check`. |
| `core_recipe_manifest.csv` | The same recipe rows as CSV, for spreadsheets. |
| `ingredients.csv` | Ingredient catalog (314 staples): canonical name, aliases (`;`-separated), store section, density (g/ml), default unit, shelf life, pack size and unit, staple flag. Load with `mealctl catalog seed`. |

## Source PDF

`Recipes_12Sept26.pdf` (174 pages, >30 MB) is household data and is **not committed** (NFR-5, and
too large for git). Put it at `data/source/Recipes_12Sept26.pdf`; that directory is git-ignored.
The M1 importer reads it from there.

A later milestone adds the store layout here.
