import pytest

from app.services.csv_safety import csv_safe, numeric_cell


@pytest.mark.parametrize("hostile", [
    "=cmd|'/C calc'!A0", "+1+1", "-1+1", "@SUM(A1:A9)", "\tHELLO", "\rHELLO",
])
def test_hostile_free_text_is_escaped(hostile):
    assert csv_safe(hostile) == "'" + hostile


def test_ordinary_free_text_is_untouched():
    assert csv_safe("AAPL") == "AAPL"
    assert csv_safe("Demo Fixture OK (not a real company)") == "Demo Fixture OK (not a real company)"


def test_empty_string_is_untouched():
    assert csv_safe("") == ""


def test_negative_number_stays_numeric_unescaped():
    """The actual bug this split design avoids: a blanket csv_safe() would
    prepend ' to every negative number (they start with '-', a trigger
    character), corrupting legitimate negative P&L/cash figures."""
    assert numeric_cell("-12.3400") == "-12.3400"


@pytest.mark.parametrize("value", ["100", "100.0200", "-0.0001", "0", "-1"])
def test_valid_numeric_strings_pass_through(value):
    assert numeric_cell(value) == value


@pytest.mark.parametrize("value", ["abc", "1e10", "", "1,000", "+1", "1.2.3"])
def test_invalid_numeric_strings_raise(value):
    with pytest.raises(ValueError):
        numeric_cell(value)
