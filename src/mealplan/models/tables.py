"""SQLAlchemy 2 tables for the core entities (requirements: "Core entities").

Pydantic schemas in `schemas.py` are the boundary types; these tables are storage only.
"""

from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from mealplan.models.enums import (
    Collection,
    InventorySource,
    Location,
    Meal,
    MealRole,
    RecipeStatus,
    SourceKind,
)


def _enum(cls: type[PyEnum]) -> Enum:
    return Enum(cls, native_enum=False, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    pass


class RecipeFamily(Base):
    """REC-9: variants of one dish (e.g. three injeras); the planner treats a family as one dish."""

    __tablename__ = "recipe_family"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    preferred_recipe_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipe.id", use_alter=True, name="fk_family_preferred_recipe")
    )

    recipes: Mapped[list["Recipe"]] = relationship(
        back_populates="family", foreign_keys="Recipe.family_id"
    )


class Recipe(Base):
    __tablename__ = "recipe"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True)  # stable id, e.g. "core-001"
    title: Mapped[str] = mapped_column(String(300))
    servings: Mapped[float | None] = mapped_column(Float)
    prep_minutes: Mapped[int | None] = mapped_column(Integer)
    cook_minutes: Mapped[int | None] = mapped_column(Integer)
    total_minutes: Mapped[int | None] = mapped_column(Integer)
    meal_role: Mapped[MealRole | None] = mapped_column(_enum(MealRole))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[RecipeStatus] = mapped_column(_enum(RecipeStatus), default=RecipeStatus.DRAFT)
    collection: Mapped[Collection] = mapped_column(_enum(Collection), default=Collection.CORE)
    family_id: Mapped[int | None] = mapped_column(ForeignKey("recipe_family.id"))
    pinned_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    household_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    family: Mapped[RecipeFamily | None] = relationship(
        back_populates="recipes", foreign_keys=[family_id]
    )
    sources: Mapped[list["RecipeSource"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="RecipeIngredient.position"
    )
    steps: Mapped[list["Step"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="Step.position"
    )


class RecipeSource(Base):
    """ING-4 / REC-3: provenance. A recipe saved several times keeps one row per copy."""

    __tablename__ = "recipe_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE"))
    kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind))
    title: Mapped[str] = mapped_column(String(300), default="")  # publisher or book
    author: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    file: Mapped[str] = mapped_column(String(300), default="")
    pages: Mapped[str] = mapped_column(String(100), default="")  # e.g. "97-98, 135"
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    recipe: Mapped[Recipe] = relationship(back_populates="sources")


class Ingredient(Base):
    """The normalization backbone: canonical names, aliases, store section, density."""

    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(200), unique=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(100), default="other")  # store section
    density_g_per_ml: Mapped[float | None] = mapped_column(Float)
    default_unit: Mapped[str | None] = mapped_column(String(40))
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    pack_size: Mapped[float | None] = mapped_column(Float)
    pack_unit: Mapped[str | None] = mapped_column(String(40))
    is_staple: Mapped[bool] = mapped_column(Boolean, default=False)  # INV-4


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)  # kept for audit (REC-2)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    qty: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))
    prep_note: Mapped[str] = mapped_column(String(300), default="")
    optional: Mapped[bool] = mapped_column(Boolean, default=False)
    # How ingredient_id was found (exact, cleaned, trailing); None means unmatched.
    match_method: Mapped[str | None] = mapped_column(String(20))

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient | None] = relationship()


class Step(Base):
    __tablename__ = "step"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    equipment: Mapped[list[str]] = mapped_column(JSON, default=list)  # oven, stove, ...
    active_minutes: Mapped[int] = mapped_column(Integer, default=0)
    passive_minutes: Mapped[int] = mapped_column(Integer, default=0)

    recipe: Mapped[Recipe] = relationship(back_populates="steps")


class Component(Base):
    """A prep-ahead item (soup, grain, dressing, chopped greens)."""

    __tablename__ = "component"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(30), default="main")  # soup, grain, protein, ...
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipe.id"))
    yield_qty: Mapped[float | None] = mapped_column(Float)
    yield_unit: Mapped[str | None] = mapped_column(String(40))
    storage: Mapped[Location] = mapped_column(_enum(Location), default=Location.FRIDGE)
    keeps_days: Mapped[int | None] = mapped_column(Integer)
    freezer_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    active_minutes: Mapped[int] = mapped_column(Integer, default=0)
    passive_minutes: Mapped[int] = mapped_column(Integer, default=0)
    equipment: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Per-serving ingredients for components without a recipe: [[name, qty, unit], ...]
    per_serving: Mapped[list[list[object]]] = mapped_column(JSON, default=list)


class MealSlot(Base):
    __tablename__ = "meal_slot"
    __table_args__ = (UniqueConstraint("date", "meal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    meal: Mapped[Meal] = mapped_column(_enum(Meal))
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipe.id"))
    component_ids: Mapped[list[int]] = mapped_column(JSON, default=list)  # lunch templates
    servings: Mapped[float] = mapped_column(Float)
    is_leftover_of: Mapped[int | None] = mapped_column(ForeignKey("meal_slot.id"))
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)  # PLN-7 manual swaps
    cooked: Mapped[bool] = mapped_column(Boolean, default=False)

    recipe: Mapped[Recipe | None] = relationship()


class WeekPlan(Base):
    """One planned week (PLN-1, PLN-7): the seed and status that reproduce its meal slots."""

    __tablename__ = "week_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)  # the prep day
    seed: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft, locked
    conflicts: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PrepSession(Base):
    """Generated, not hand-entered (PLN-5)."""

    __tablename__ = "prep_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True)
    component_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    tasks: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    est_minutes: Mapped[int] = mapped_column(Integer, default=0)


class InventoryItem(Base):
    __tablename__ = "inventory_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"))
    qty: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(40))
    location: Mapped[Location] = mapped_column(_enum(Location))
    added_on: Mapped[date] = mapped_column(Date)
    best_by: Mapped[date | None] = mapped_column(Date)
    source: Mapped[InventorySource] = mapped_column(_enum(InventorySource))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class ShoppingList(Base):
    __tablename__ = "shopping_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)

    lines: Mapped[list["ShoppingLine"]] = relationship(
        back_populates="shopping_list", cascade="all, delete-orphan"
    )


class ShoppingLine(Base):
    __tablename__ = "shopping_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    shopping_list_id: Mapped[int] = mapped_column(
        ForeignKey("shopping_list.id", ondelete="CASCADE")
    )
    # Null for lines the normalizer could not match: they stay on the list, flagged.
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    name: Mapped[str] = mapped_column(String(300), default="")
    unit: Mapped[str] = mapped_column(String(40))
    qty_needed: Mapped[float] = mapped_column(Float)
    qty_on_hand: Mapped[float] = mapped_column(Float, default=0.0)
    qty_to_buy: Mapped[float] = mapped_column(Float)
    pack_size: Mapped[float | None] = mapped_column(Float)
    pack_unit: Mapped[str | None] = mapped_column(String(40))
    packs: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    section: Mapped[str] = mapped_column(String(100), default="other")
    retailer_sku: Mapped[str | None] = mapped_column(String(100))

    shopping_list: Mapped[ShoppingList] = relationship(back_populates="lines")


class Rating(Base):
    __tablename__ = "rating"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    score: Mapped[int] = mapped_column(Integer)  # 1-5
    would_repeat: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str] = mapped_column(Text, default="")


class Preference(Base):
    """Household rules: dislikes, spice level, max weeknight minutes."""

    __tablename__ = "preference"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


class SkuMatch(Base):
    """RTL-3: remembered ingredient-to-product matches per retailer."""

    __tablename__ = "sku_match"
    __table_args__ = (UniqueConstraint("retailer", "ingredient_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    retailer: Mapped[str] = mapped_column(String(50))
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"))
    sku: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="")


class AgentCall(Base):
    """NFR-7: every agent call is logged with model, tokens, latency, and outcome."""

    __tablename__ = "agent_call"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(50))  # ok, retry, failed
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IngestFailure(Base):
    """Guardrails: imports that failed validation or grounding after one retry."""

    __tablename__ = "ingest_failure"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), default="")  # manifest id, if any
    source_file: Mapped[str] = mapped_column(String(300), default="")
    pages: Mapped[str] = mapped_column(String(100), default="")
    stage: Mapped[str] = mapped_column(String(30))  # validation, grounding, refusal, api
    error: Mapped[str] = mapped_column(Text)
    raw_output: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
