"""Household rules for the planner (PLN-1), stored in the `preference` key/value table.

Defaults are the household's answers from M2 planning: dinner for 4, two people packing the
same lunch Monday to Friday, 45 minutes hands-on on weeknights, and nothing very spicy.
"""

import json
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mealplan.models.tables import Preference


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"

    @classmethod
    def of(cls, day: date) -> "Weekday":
        return list(cls)[day.weekday()]


WORKDAYS = [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI]


class HouseholdPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    dinner_servings: int = Field(default=4, ge=1)
    lunch_servings: int = Field(default=2, ge=0)
    lunch_days: list[Weekday] = Field(default_factory=lambda: list(WORKDAYS))
    prep_day: Weekday = Weekday.SUN
    weeknight_days: list[Weekday] = Field(default_factory=lambda: list(WORKDAYS))
    max_weeknight_active_minutes: int = Field(default=45, ge=0)
    max_spice: int = Field(default=2, ge=0, le=3)  # 3 = very spicy
    avoid_ingredients: list[str] = Field(default_factory=list)  # catalog canonical names
    avoid_tags: list[str] = Field(default_factory=lambda: ["very-spicy"])
    max_protein_repeats: int = Field(default=2, ge=1)
    max_discovered_dinners: int = Field(default=1, ge=0)
    max_leftover_lunches: int = Field(default=3, ge=0)
    leftover_exclude_proteins: list[str] = Field(default_factory=lambda: ["fish", "shellfish"])
    repeat_window_days: int = Field(default=14, ge=0)
    # Store sections in walking order for the shopping list (SHP-4); others go last.
    store_layout: list[str] = Field(
        default_factory=lambda: [
            "produce",
            "bakery",
            "deli",
            "meat",
            "seafood",
            "dairy",
            "refrigerated",
            "frozen",
            "canned",
            "grains",
            "international",
            "baking",
            "spices",
            "oils",
            "condiments",
            "nuts",
            "beverages",
            "pantry",
        ]
    )
    # Standing dinners every week starts from: weekday -> recipe ref, or refs separated by
    # "|" to rotate week by week. Days not listed are chosen by the planner.
    base_week: dict[Weekday, str] = Field(
        default_factory=lambda: {
            Weekday.MON: "house-001",  # salmon, jasmine rice, broccoli
            Weekday.TUE: "house-002|house-003",  # tacos: refried beans, then meat
            Weekday.FRI: "house-004",  # pizza, ordered in
        }
    )


class PrefsError(ValueError):
    pass


def load_prefs(session: Session) -> HouseholdPrefs:
    stored = {p.key: p.value for p in session.scalars(select(Preference))}
    known = {k: v for k, v in stored.items() if k in HouseholdPrefs.model_fields}
    return HouseholdPrefs.model_validate(known)


def set_pref(session: Session, key: str, raw: str) -> HouseholdPrefs:
    """Set one preference from CLI text: JSON if it parses, else a plain string.

    Comma-separated text is accepted for list fields ("mon,tue,wed").
    """
    if key not in HouseholdPrefs.model_fields:
        raise PrefsError(
            f"unknown preference {key!r}; known: {', '.join(HouseholdPrefs.model_fields)}"
        )
    value: Any
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    annotation = str(HouseholdPrefs.model_fields[key].annotation)
    if annotation.startswith("list") and isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    prefs = load_prefs(session)
    try:
        setattr(prefs, key, value)
    except ValidationError as e:
        raise PrefsError(str(e)) from None
    row = session.get(Preference, key)
    stored = prefs.model_dump(mode="json")[key]
    if row is None:
        session.add(Preference(key=key, value=stored))
    else:
        row.value = stored
    session.flush()
    return prefs
