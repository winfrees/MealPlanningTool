"""Ingredient catalog and name normalizer (the normalization backbone).

``Catalog.match`` maps a parsed ingredient name ("boneless skinless chicken thighs") to one
catalog entry ("chicken thigh") and says how it got there. Unmatched names return ``None`` and
go to review; the normalizer never invents an ingredient (risk: "never guess silently").
"""

import csv
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.core.units import canonical_unit
from mealplan.models.tables import Ingredient

DESCRIPTORS = frozenset(
    (
        "small",
        "medium",
        "large",
        "extra-large",
        "jumbo",
        "fresh",
        "freshly",
        "chopped",
        "finely",
        "roughly",
        "coarsely",
        "thinly",
        "minced",
        "diced",
        "sliced",
        "grated",
        "shredded",
        "peeled",
        "seeded",
        "cubed",
        "cooked",
        "uncooked",
        "raw",
        "packed",
        "softened",
        "melted",
        "cold",
        "boneless",
        "skinless",
        "bone-in",
        "skin-on",
        "organic",
        "ripe",
        "trimmed",
        "halved",
        "quartered",
        "homemade",
    )
)
_IRREGULAR_SINGULARS = {"leaves": "leaf", "halves": "half", "loaves": "loaf"}


class MatchMethod(StrEnum):
    EXACT = "exact"  # canonical name or alias as written
    CLEANED = "cleaned"  # after dropping descriptors and plurals
    TRAILING = "trailing"  # the trailing words of the name (head noun)


@dataclass(frozen=True)
class CatalogEntry:
    canonical_name: str
    aliases: tuple[str, ...] = ()
    section: str = "other"
    density_g_per_ml: float | None = None
    default_unit: str | None = None
    shelf_life_days: int | None = None
    pack_size: float | None = None
    pack_unit: str | None = None
    staple: bool = False


@dataclass(frozen=True)
class Match:
    entry: CatalogEntry
    method: MatchMethod


def _key(text: str) -> str:
    text = text.lower().replace("\u2019", "'")
    text = re.sub(r"[^\w\s'&%-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def singular(word: str) -> str:
    if word in _IRREGULAR_SINGULARS:
        return _IRREGULAR_SINGULARS[word]
    if len(word) <= 3 or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("oes", "ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def _singular_phrase(phrase: str) -> str:
    words = phrase.split()
    return " ".join([*words[:-1], singular(words[-1])]) if words else phrase


class Catalog:
    def __init__(self, entries: Iterable[CatalogEntry]) -> None:
        self._entries: dict[str, CatalogEntry] = {}
        self._index: dict[str, CatalogEntry] = {}
        for entry in entries:
            if entry.canonical_name in self._entries:
                raise ValueError(f"duplicate ingredient {entry.canonical_name!r}")
            self._entries[entry.canonical_name] = entry
        # Exact names first so an alias can never shadow a canonical name.
        for entry in self._entries.values():
            self._add(_key(entry.canonical_name), entry)
        for entry in self._entries.values():
            for alias in entry.aliases:
                self._add(_key(alias), entry)

    def _add(self, key: str, entry: CatalogEntry) -> None:
        self._index.setdefault(key, entry)
        self._index.setdefault(_singular_phrase(key), entry)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[CatalogEntry]:
        return iter(self._entries.values())

    def get(self, canonical_name: str) -> CatalogEntry:
        return self._entries[canonical_name]

    def _lookup(self, phrase: str) -> CatalogEntry | None:
        return self._index.get(phrase) or self._index.get(_singular_phrase(phrase))

    def match(self, name: str) -> Match | None:
        key = _key(name)
        if not key:
            return None
        if entry := self._lookup(key):
            return Match(entry, MatchMethod.EXACT)

        first_choice = re.split(r"\s+or\s+", key)[0]  # "chicken broth or water"
        words = [w for w in first_choice.split() if w not in DESCRIPTORS]
        cleaned = " ".join(words)
        if cleaned and (entry := self._lookup(cleaned)):
            return Match(entry, MatchMethod.CLEANED)
        if " and " in f" {cleaned} ":
            return None  # compound ("salt and pepper"): a person decides
        for start in range(1, len(words)):
            if entry := self._lookup(" ".join(words[start:])):
                return Match(entry, MatchMethod.TRAILING)
        return None

    @classmethod
    def from_csv(cls, path: Path) -> "Catalog":
        with path.open(encoding="utf-8", newline="") as f:
            return cls(_entry_from_row(row) for row in csv.DictReader(f))

    @classmethod
    def from_db(cls, session: Session) -> "Catalog":
        return cls(
            CatalogEntry(
                canonical_name=i.canonical_name,
                aliases=tuple(i.aliases),
                section=i.category,
                density_g_per_ml=i.density_g_per_ml,
                default_unit=i.default_unit,
                shelf_life_days=i.shelf_life_days,
                pack_size=i.pack_size,
                pack_unit=i.pack_unit,
                staple=i.is_staple,
            )
            for i in session.scalars(select(Ingredient))
        )


def _opt_float(v: str) -> float | None:
    return float(v) if v.strip() else None


def _opt_unit(v: str, field: str, name: str) -> str | None:
    if not v.strip():
        return None
    unit = canonical_unit(v)
    if unit is None:
        raise ValueError(f"{name}: unknown {field} {v!r}")
    return unit


def _entry_from_row(row: dict[str, str]) -> CatalogEntry:
    name = row["canonical_name"].strip()
    shelf = row["shelf_life_days"].strip()
    return CatalogEntry(
        canonical_name=name,
        aliases=tuple(a.strip() for a in row["aliases"].split(";") if a.strip()),
        section=row["section"].strip() or "other",
        density_g_per_ml=_opt_float(row["density_g_per_ml"]),
        default_unit=_opt_unit(row["default_unit"], "default_unit", name),
        shelf_life_days=int(shelf) if shelf else None,
        pack_size=_opt_float(row["pack_size"]),
        pack_unit=_opt_unit(row["pack_unit"], "pack_unit", name),
        staple=row["staple"].strip().lower() == "yes",
    )


def seed_catalog(session: Session, catalog: Catalog) -> tuple[int, int]:
    """Insert or update every catalog entry by canonical name. Returns (added, updated)."""
    existing = {i.canonical_name: i for i in session.scalars(select(Ingredient))}
    added = updated = 0
    for e in catalog:
        row = existing.get(e.canonical_name)
        if row is None:
            row = Ingredient(canonical_name=e.canonical_name)
            session.add(row)
            added += 1
        else:
            updated += 1
        row.aliases = list(e.aliases)
        row.category = e.section
        row.density_g_per_ml = e.density_g_per_ml
        row.default_unit = e.default_unit
        row.shelf_life_days = e.shelf_life_days
        row.pack_size = e.pack_size
        row.pack_unit = e.pack_unit
        row.is_staple = e.staple
    session.flush()
    return added, updated
