"""Ingredient catalog (data/ingredients.csv) and name matching."""

from pathlib import Path

import pytest

from mealplan import db
from mealplan.core.normalizer import (
    Catalog,
    CatalogEntry,
    MatchMethod,
    seed_catalog,
    singular,
)
from mealplan.core.parser import parse_ingredient

CATALOG_CSV = Path(__file__).resolve().parent.parent / "data" / "ingredients.csv"


def test_catalog_is_seeded_with_about_300_staples(catalog):
    assert len(catalog) >= 300


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("tomatoes", "tomato"),
        ("berries", "berry"),
        ("thighs", "thigh"),
        ("peaches", "peach"),
        ("leaves", "leaf"),
        ("chickpeas", "chickpea"),
        ("hummus", "hummus"),
        ("glass", "glass"),
        ("peas", "pea"),
    ],
)
def test_singular(word, expected):
    assert singular(word) == expected


@pytest.mark.parametrize(
    ("name", "canonical", "method"),
    [
        ("onion", "onion", MatchMethod.EXACT),
        ("scallions", "green onion", MatchMethod.EXACT),
        ("eggs", "egg", MatchMethod.EXACT),
        ("Greek yogurt", "greek yogurt", MatchMethod.EXACT),
        ("chickpeas", "chickpeas", MatchMethod.EXACT),
        ("garbanzo beans", "chickpeas", MatchMethod.EXACT),
        ("hot sauce", "hot sauce", MatchMethod.EXACT),
        ("large onion", "onion", MatchMethod.CLEANED),
        ("finely chopped fresh parsley", "parsley", MatchMethod.CLEANED),
        ("boneless skinless chicken thighs", "chicken thigh", MatchMethod.CLEANED),
        ("medium sweet potatoes", "sweet potato", MatchMethod.CLEANED),
        ("cooked quinoa", "quinoa", MatchMethod.CLEANED),
        ("chicken broth or water", "chicken broth", MatchMethod.CLEANED),
        ("diced tomatoes", "diced tomatoes", MatchMethod.EXACT),
        ("ground cumin", "cumin", MatchMethod.EXACT),
        ("berbere spice", "berbere", MatchMethod.EXACT),
        ("niter kibbeh", "niter kibbeh", MatchMethod.EXACT),
        ("teff flour", "teff flour", MatchMethod.EXACT),
        ("jalapeños", "jalapeño", MatchMethod.EXACT),
        ("red lentils", "red lentils", MatchMethod.EXACT),
        ("skinless chicken drumsticks", "chicken drumstick", MatchMethod.CLEANED),
        ("extra-virgin olive oil", "olive oil", MatchMethod.EXACT),
        ("freshly grated parmesan", "parmesan", MatchMethod.CLEANED),
        ("organic baby spinach", "spinach", MatchMethod.CLEANED),
        ("roasted chicken stock", "chicken broth", MatchMethod.TRAILING),
    ],
)
def test_match(catalog, name, canonical, method):
    m = catalog.match(name)
    assert m is not None, name
    assert m.entry.canonical_name == canonical
    assert m.method is method


@pytest.mark.parametrize("name", ["salt and pepper", "", "unobtainium", "juice"])
def test_unknown_or_compound_names_are_not_guessed(catalog, name):
    assert catalog.match(name) is None


def test_parsed_golden_lines_mostly_match(catalog):
    """Early signal for the M1 target (95%+ of lines need no edits)."""
    import json

    golden_path = CATALOG_CSV.parent.parent / "tests" / "golden" / "ingredient_lines.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    names = [parse_ingredient(c["raw"]).name for c in golden]
    unmatched = [n for n in names if catalog.match(n) is None]
    assert len(unmatched) / len(names) <= 0.05, unmatched


def test_duplicate_entries_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        Catalog([CatalogEntry("salt"), CatalogEntry("salt")])


def test_canonical_name_wins_over_alias():
    catalog = Catalog([CatalogEntry("pepper", ()), CatalogEntry("black pepper", ("pepper",))])
    m = catalog.match("pepper")
    assert m is not None and m.entry.canonical_name == "pepper"


def test_bad_unit_in_csv_is_rejected(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "canonical_name,aliases,section,density_g_per_ml,default_unit,shelf_life_days,"
        "pack_size,pack_unit,staple\nsalt,,spices,,furlong,,,,yes\n"
    )
    with pytest.raises(ValueError, match="furlong"):
        Catalog.from_csv(bad)


def test_seed_catalog_is_idempotent(db_url, catalog):
    db.upgrade(db_url)
    engine = db.make_engine(db_url)
    with db.session_scope(engine) as s:
        assert seed_catalog(s, catalog) == (len(catalog), 0)
    with db.session_scope(engine) as s:
        assert seed_catalog(s, catalog) == (0, len(catalog))
        from_db = Catalog.from_db(s)
    assert len(from_db) == len(catalog)
    assert from_db.get("all-purpose flour") == catalog.get("all-purpose flour")
