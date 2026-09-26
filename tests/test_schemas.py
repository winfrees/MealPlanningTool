import pytest
from pydantic import ValidationError

from mealplan.models.enums import SourceKind
from mealplan.models.schemas import RecipeDraft, SourceRef


def test_recipe_draft_requires_a_source():
    with pytest.raises(ValidationError):
        RecipeDraft(title="Doro Wat", sources=[])


def test_recipe_draft_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        RecipeDraft.model_validate({"title": "Doro Wat", "sources": [{"kind": "pdf"}], "rating": 5})


def test_recipe_draft_valid():
    draft = RecipeDraft(
        title="  Doro Wat  ",
        sources=[SourceRef(kind=SourceKind.PDF, file="Recipes_12Sept26.pdf", pages=[1, 2, 3])],
    )
    assert draft.title == "Doro Wat"
