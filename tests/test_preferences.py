import pytest

from mealplan.core.preferences import PrefsError, Weekday, load_prefs, set_pref


def test_defaults_are_the_household_answers(session):
    prefs = load_prefs(session)
    assert prefs.dinner_servings == 4
    assert prefs.lunch_servings == 2
    assert prefs.lunch_days == [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI]
    assert prefs.max_weeknight_active_minutes == 45
    assert prefs.max_spice == 2
    assert "very-spicy" in prefs.avoid_tags
    assert prefs.prep_day is Weekday.SUN


def test_set_and_reload(session):
    set_pref(session, "dinner_servings", "3")
    set_pref(session, "lunch_days", "mon,wed,fri")
    set_pref(session, "avoid_ingredients", '["shrimp"]')
    prefs = load_prefs(session)
    assert prefs.dinner_servings == 3
    assert prefs.lunch_days == [Weekday.MON, Weekday.WED, Weekday.FRI]
    assert prefs.avoid_ingredients == ["shrimp"]


@pytest.mark.parametrize(
    ("key", "value"),
    [("dinner_servings", "0"), ("max_spice", "7"), ("lunch_days", "funday"), ("nope", "1")],
)
def test_invalid_values_are_rejected(session, key, value):
    with pytest.raises(PrefsError):
        set_pref(session, key, value)
    assert load_prefs(session).dinner_servings == 4
