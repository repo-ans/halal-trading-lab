from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.market_calendar import session_bounds
from app.providers.price.base import Bar
from app.services.validation import (
    detect_close_indexed_days,
    expected_bar_count,
    is_forming,
    validate_bar,
)

OPEN = datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)
CLOSE = datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)


def bar(ts=None, open=Decimal("10"), high=Decimal("11"), low=Decimal("9"),
       close=Decimal("10.5"), volume=100) -> Bar:
    return Bar(ts=ts or OPEN, open=open, high=high, low=low, close=close, volume=volume)


def test_valid_bar_passes():
    assert validate_bar(bar(), OPEN, CLOSE) is None


def test_high_below_low_rejected():
    assert validate_bar(bar(high=Decimal("5"), low=Decimal("9")), OPEN, CLOSE) is not None


def test_open_outside_range_rejected():
    assert validate_bar(bar(open=Decimal("100")), OPEN, CLOSE) is not None


def test_close_outside_range_rejected():
    assert validate_bar(bar(close=Decimal("100")), OPEN, CLOSE) is not None


def test_non_positive_price_rejected():
    assert validate_bar(bar(low=Decimal("0"), open=Decimal("0")), OPEN, CLOSE) is not None
    assert validate_bar(bar(low=Decimal("-1"), open=Decimal("-1")), OPEN, CLOSE) is not None


def test_negative_volume_rejected():
    assert validate_bar(bar(volume=-1), OPEN, CLOSE) is not None


def test_off_grid_timestamp_rejected():
    off_grid = OPEN.replace(minute=32)
    assert validate_bar(bar(ts=off_grid), OPEN, CLOSE) is not None


def test_naive_timestamp_rejected():
    naive = OPEN.replace(tzinfo=None)
    assert validate_bar(bar(ts=naive), OPEN, CLOSE) is not None


def test_timestamp_outside_session_rejected():
    before_open = OPEN.replace(hour=12)
    assert validate_bar(bar(ts=before_open), OPEN, CLOSE) is not None
    at_close = CLOSE  # bar at exactly session_close -- its 5 minutes would run past close
    assert validate_bar(bar(ts=at_close), OPEN, CLOSE) is not None


def test_last_bar_of_session_is_valid():
    last_open = CLOSE.replace(hour=20, minute=55)  # 20:55 UTC, closes exactly at 21:00
    assert validate_bar(bar(ts=last_open), OPEN, CLOSE) is None


def test_expected_bar_count_normal_day():
    assert expected_bar_count(OPEN, CLOSE) == 78


def test_expected_bar_count_half_day():
    half_close = OPEN.replace(hour=18, minute=0)
    assert expected_bar_count(OPEN, half_close) == 42


def test_expected_bar_count_matches_summer_and_winter_sessions():
    """Session length is 6.5h either side of DST -- only the UTC clock times shift
    (see test_market_calendar.py), not the bar count."""
    winter = session_bounds(date(2026, 12, 15))
    summer = session_bounds(date(2027, 7, 15))
    assert expected_bar_count(*winter) == 78
    assert expected_bar_count(*summer) == 78


def test_expected_bar_count_real_half_day():
    half = session_bounds(date(2027, 11, 26))  # day after Thanksgiving
    assert expected_bar_count(*half) == 42


def test_is_forming_true_for_bar_not_yet_closed():
    now = OPEN.replace(minute=32)  # bar opened at 14:30 closes at 14:35; now is 14:32
    assert is_forming(bar(ts=OPEN), now) is True


def test_close_indexed_timestamp_cannot_be_detected_per_bar():
    """FLAGGED LIMITATION (see yfinance_provider.py and docs/HANDOVER.md): the
    provider assumes yfinance's intraday index is bar-OPEN time. If it were actually
    bar-CLOSE time instead, every bar.ts would be shifted +5 minutes from what we
    think it is. This test proves that shift is INVISIBLE to validate_bar ON A SINGLE
    INTERIOR BAR -- a close-indexed bar still lands on the 5-minute grid and still
    falls inside the session, so it passes every per-bar check here. The SET-level
    check (detect_close_indexed_days, tested below) is what catches it, by looking at
    the day's boundary bars instead of any one bar in isolation."""
    open_indexed = bar(ts=OPEN)  # what we assume: ts == bar open
    close_indexed = bar(ts=OPEN.replace(minute=35))  # what it would be if ts == bar close
    assert validate_bar(open_indexed, OPEN, CLOSE) is None
    assert validate_bar(close_indexed, OPEN, CLOSE) is None  # also "valid" -- the point


def test_detects_close_indexed_day():
    """Simulates a full day of CLOSE-indexed bars: every real bar's reported ts is its
    close time, not open -- so the earliest ts present is session_open + 5min (never
    session_open itself) and the latest is exactly session_close. That exact pattern
    -- open missing, close present -- is the signal."""
    day = date(2026, 12, 15)  # winter day, real calendar (see test_market_calendar.py)
    day_open, day_close = session_bounds(day)
    n = expected_bar_count(day_open, day_close)
    close_indexed_bars = [bar(ts=day_open + timedelta(minutes=5 * (i + 1))) for i in range(n)]

    assert detect_close_indexed_days(close_indexed_bars, "NYSE") == [day]


def test_open_indexed_day_not_flagged():
    day = date(2026, 12, 15)
    day_open, day_close = session_bounds(day)
    n = expected_bar_count(day_open, day_close)
    open_indexed_bars = [bar(ts=day_open + timedelta(minutes=5 * i)) for i in range(n)]

    assert detect_close_indexed_days(open_indexed_bars, "NYSE") == []


def test_missing_open_alone_is_not_enough_to_flag():
    """Only "open missing AND close present" triggers it -- an ordinary partial day
    (say, the provider just hasn't returned the first few bars yet) must not be
    misdiagnosed as an indexing bug."""
    day = date(2026, 12, 15)
    day_open, day_close = session_bounds(day)
    partial_bars = [bar(ts=day_open + timedelta(minutes=30))]  # missing open, no close either

    assert detect_close_indexed_days(partial_bars, "NYSE") == []


def test_is_forming_false_once_bar_has_closed():
    now = OPEN.replace(minute=35)  # exactly when the bar closes
    assert is_forming(bar(ts=OPEN), now) is False
    later = OPEN.replace(minute=40)
    assert is_forming(bar(ts=OPEN), later) is False
