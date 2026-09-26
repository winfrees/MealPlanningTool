"""Enumerations shared by the database tables, schemas, and agents."""

from enum import StrEnum


class RecipeStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class Collection(StrEnum):
    """REC-7: the household's own recipes vs. ones found by agents."""

    CORE = "core"
    DISCOVERED = "discovered"


class SourceKind(StrEnum):
    PDF = "pdf"
    URL = "url"
    PHOTO = "photo"
    MANUAL = "manual"


class MealRole(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SOUP = "soup"
    SIDE = "side"
    BREAD = "bread"
    DESSERT = "dessert"
    CONDIMENT = "condiment"
    SNACK = "snack"


class Meal(StrEnum):
    LUNCH = "lunch"
    DINNER = "dinner"


class Location(StrEnum):
    FRIDGE = "fridge"
    FREEZER = "freezer"
    PANTRY = "pantry"


class InventorySource(StrEnum):
    PHOTO = "photo"
    RECEIPT = "receipt"
    MANUAL = "manual"
    CONSUMED_BY_PLAN = "consumed-by-plan"
    PREP_YIELD = "prep-yield"
