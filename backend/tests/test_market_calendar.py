"""Session hours must come from pandas_market_calendars, never from a constant.
Stage E's end-of-day exit depends on this being right."""
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.market_calendar import session_bounds
from app.providers.price.mock import SESSION_OPEN_UTC


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_winter_day_est():
    assert session_bounds(date(2026, 12, 15)) == (utc(2026, 12, 15, 14, 30), utc(2026, 12, 15, 21, 0))


def test_summer_day_edt():
    assert session_bounds(date(2027, 7, 15)) == (utc(2027, 7, 15, 13, 30), utc(2027, 7, 15, 20, 0))


def test_half_day_day_after_thanksgiving():
    assert session_bounds(date(2027, 11, 26)) == (utc(2027, 11, 26, 14, 30), utc(2027, 11, 26, 18, 0))


@pytest.mark.parametrize("day", [date(2027, 11, 25), date(2026, 12, 25), date(2026, 12, 19)])
def test_closed_days_have_no_session(day):  # Thanksgiving, Christmas, Saturday
    assert session_bounds(day) is None


def test_fixture_constant_is_wrong_in_summer_so_it_must_never_be_used_by_the_engine():
    winter_open, _ = session_bounds(date(2026, 12, 15))
    summer_open, _ = session_bounds(date(2027, 7, 15))
    assert winter_open.time() == SESSION_OPEN_UTC  # fixture matches winter only
    assert summer_open.time() != SESSION_OPEN_UTC  # ...and is an hour off in summer


def test_no_hardcoded_session_times_outside_the_mock_fixture():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"SESSION_OPEN_UTC|time\(\s*(13|14|20|21)\s*,\s*(0|30)\s*\)")
    offenders = [str(p) for p in app_dir.rglob("*.py")
                 if p.name != "mock.py" and pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
