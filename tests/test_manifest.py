import pytest
from pydantic import ValidationError

from mealplan.models.enums import MealRole
from mealplan.models.manifest import load_manifest, parse_pages


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("10", [10]),
        ("1-3", [1, 2, 3]),
        ("80, 106-107", [80, 106, 107]),
        ("97-98, 135, 146", [97, 98, 135, 146]),
    ],
)
def test_parse_pages(spec, expected):
    assert parse_pages(spec) == expected


def test_parse_pages_rejects_descending_range():
    with pytest.raises(ValueError, match="descending"):
        parse_pages("9-3")


def test_core_manifest_matches_requirements(manifest_path):
    """Figures from docs/requirements.md, "Core recipe collection"."""
    manifest = load_manifest(manifest_path)
    assert manifest.pages == 174
    assert len(manifest.recipes) == 93

    by_role: dict[MealRole, int] = {}
    for r in manifest.recipes:
        by_role[r.meal_role] = by_role.get(r.meal_role, 0) + 1
    assert by_role[MealRole.DINNER] == 47
    assert by_role[MealRole.DESSERT] == 16

    families = {name: len(members) for name, members in manifest.families().items()}
    assert families == {"injera": 3, "butter-chicken": 2, "shakshuka": 2, "sourdough": 2}


def test_every_manifest_page_is_within_the_pdf(manifest_path):
    manifest = load_manifest(manifest_path)
    for r in manifest.recipes:
        assert all(1 <= p <= manifest.pages for p in r.page_list), r.id


def test_manifest_rejects_duplicate_ids(manifest_path):
    raw = load_manifest(manifest_path).model_dump()
    raw["recipes"].append(raw["recipes"][0])
    from mealplan.models.manifest import Manifest

    with pytest.raises(ValidationError, match="duplicate"):
        Manifest.model_validate(raw)
