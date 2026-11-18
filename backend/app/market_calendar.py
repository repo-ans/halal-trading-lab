"""Session open/close come from pandas_market_calendars ONLY. Never hardcode market
hours or holidays (CLAUDE.md section 8). Half days and DST are handled by the library."""
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas_market_calendars as mcal

# MIC code -> pandas_market_calendars calendar name. The single source of truth for
# this mapping -- backfill.py and the /instruments routes both import it from here
# rather than each keeping their own copy. Only the exchanges app/seed/instruments.json
# actually uses.
EXCHANGE_CALENDARS = {"XNAS": "NASDAQ", "XNYS": "NYSE", "XTSE": "XTSE"}


@lru_cache
def _get_calendar(calendar: str):
    # mcal.get_calendar() is expensive to construct (loads the full holiday schedule);
    # callers that compute bounds over many days (e.g. the backfill gap report) would
    # otherwise rebuild it on every single day.
    return mcal.get_calendar(calendar)


def session_bounds(day: date, calendar: str = "NYSE") -> tuple[datetime, datetime] | None:
    """(open, close) as tz-aware UTC datetimes, or None if the market is closed that day."""
    cal = _get_calendar(calendar)
    sched = cal.schedule(start_date=day, end_date=day)
    if sched.empty:
        return None
    row = sched.iloc[0]
    return (row["market_open"].to_pydatetime().astimezone(timezone.utc),
            row["market_close"].to_pydatetime().astimezone(timezone.utc))


def exchange_timezone(calendar: str) -> str:
    """The IANA timezone name (e.g. "America/New_York") for a calendar name -- the
    single place this mapping lives, so the frontend never hardcodes exchange ->
    timezone (CLAUDE.md section 8: convert at the display layer only, from one
    source of truth)."""
    return str(_get_calendar(calendar).tz)


def trading_sessions(calendar: str, start: date, end: date) -> list[date]:
    """Ascending trading-session dates in [start, end] -- weekends and holidays
    already excluded by the calendar. Stage D (app/services/strategy.py) uses this
    to build the calendar-expected bar schedule; it never hardcodes which days the
    market is open."""
    cal = _get_calendar(calendar)
    sched = cal.schedule(start_date=start, end_date=end)
    return [ts.date() for ts in sched.index]


def session_bar_opens(day: date, calendar: str = "NYSE", interval_minutes: int = 5) -> list[datetime]:
    """Ascending bar-OPEN timestamps (UTC) for one session's bars, or [] if the
    market is closed that day. The last bar opens at (close - interval) -- e.g. NYSE
    9:30-16:00 is 390 minutes, giving 78 five-minute bars (CLAUDE.md section 6's
    sizing estimate). This is the calendar-only source for execution_ts and
    gap-aware evaluability in app/services/strategy.py -- never derived from
    observed price_bars."""
    bounds = session_bounds(day, calendar)
    if bounds is None:
        return []
    open_, close_ = bounds
    step = timedelta(minutes=interval_minutes)
    opens = []
    t = open_
    while t < close_:
        opens.append(t)
        t += step
    return opens
