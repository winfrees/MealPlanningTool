"""Pydantic boundary types. Everything crossing into the core is validated here."""

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from mealplan.models.enums import Collection, MealRole, SourceKind


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceRef(Schema):
    kind: SourceKind
    title: str = ""
    author: str = ""
    url: HttpUrl | None = None
    file: str = ""
    pages: list[int] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class IngredientLine(Schema):
    raw_text: str = Field(min_length=1)
    name: str | None = None
    qty: float | None = Field(default=None, ge=0)
    unit: str | None = None
    prep_note: str = ""
    optional: bool = False


class StepDraft(Schema):
    text: str = Field(min_length=1)
    equipment: list[str] = Field(default_factory=list)
    active_minutes: int = Field(default=0, ge=0)
    passive_minutes: int = Field(default=0, ge=0)


class RecipeDraft(Schema):
    """What an importer or the extraction agent proposes; lands in the review queue (ING-3)."""

    title: str = Field(min_length=1)
    servings: float | None = Field(default=None, gt=0)
    prep_minutes: int | None = Field(default=None, ge=0)
    cook_minutes: int | None = Field(default=None, ge=0)
    total_minutes: int | None = Field(default=None, ge=0)
    meal_role: MealRole | None = None
    tags: list[str] = Field(default_factory=list)
    collection: Collection = Collection.CORE
    ingredients: list[IngredientLine] = Field(default_factory=list)
    steps: list[StepDraft] = Field(default_factory=list)
    sources: list[SourceRef] = Field(min_length=1)


class RecipeEdit(Schema):
    """A person's edit of a recipe (UI-5): the fields a reviewer fixes after an import.
    Ingredients are lines as written; they are parsed and matched again on save."""

    title: str = Field(min_length=1, max_length=200)
    servings: float | None = Field(default=None, gt=0)
    prep_minutes: int | None = Field(default=None, ge=0)
    cook_minutes: int | None = Field(default=None, ge=0)
    total_minutes: int | None = Field(default=None, ge=0)
    meal_role: MealRole | None = None
    tags: list[str] = Field(default_factory=list)
    household_notes: str = Field(default="", max_length=2000)
    ingredients: list[str] = Field(default_factory=list, max_length=200)
    steps: list[StepDraft] = Field(default_factory=list, max_length=100)
