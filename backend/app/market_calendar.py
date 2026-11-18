"""Session open/close come from pandas_market_calendars ONLY. Never hardcode market
hours or holidays (CLAUDE.md section 8). Half days and DST are handled by the library."""
from datetime import date, datetime, timezone
from functools import lru_cache

import pandas_market_calendars as mcal


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
