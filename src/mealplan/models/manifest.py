"""The core recipe manifest: an index of the household's digitized collection (REC-7)."""

import json
from collections import defaultdict
from pathlib import Path

from pydantic import Field, field_validator

from mealplan.models.enums import MealRole
from mealplan.models.schemas import Schema


def parse_pages(spec: str) -> list[int]:
    """Parse a page spec like ``"80, 106-107"`` into ``[80, 106, 107]``."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(p) for p in part.split("-", 1))
            if end < start:
                raise ValueError(f"descending page range: {part!r}")
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return pages


class ManifestRecipe(Schema):
    id: str = Field(pattern=r"^core-\d{3}$")
    title: str = Field(min_length=1)
    source: str
    url: str = ""
    pages: str
    format: str
    meal_role: MealRole
    variant_family: str = ""
    household_notes: str = ""
    copies: str = ""

    @field_validator("pages")
    @classmethod
    def _pages_parse(cls, v: str) -> str:
        if not parse_pages(v):
            raise ValueError("pages must not be empty")
        return v

    @property
    def page_list(self) -> list[int]:
        return parse_pages(self.pages)


class NonRecipePages(Schema):
    item: str
    pages: str
    note: str = ""


class Manifest(Schema):
    source_pdf: str
    pages: int = Field(gt=0)
    rules: dict[str, str] = Field(default_factory=dict)
    recipes: list[ManifestRecipe]
    non_recipe_pages: list[NonRecipePages] = Field(default_factory=list)

    @field_validator("recipes")
    @classmethod
    def _unique_ids(cls, v: list[ManifestRecipe]) -> list[ManifestRecipe]:
        ids = [r.id for r in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate recipe ids in manifest")
        return v

    def families(self) -> dict[str, list[ManifestRecipe]]:
        groups: dict[str, list[ManifestRecipe]] = defaultdict(list)
        for r in self.recipes:
            if r.variant_family:
                groups[r.variant_family].append(r)
        return dict(groups)


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
