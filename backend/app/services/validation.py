"""Pure validation rules for bars before insert (CLAUDE.md section 4, item 4). No DB,
no network -- every function here takes plain values and is directly unit-testable.
Rejected bars are never silently dropped: callers must log the reason this module
returns, not just discard the bar."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.market_calendar import session_bounds
from app.providers.price.base import Bar

BAR_SECONDS = 300
LARGE_JUMP_THRESHOLD = Decimal("0.25")


def validate_bar(bar: Bar, session_open: datetime, session_close: datetime) -> str | None:
    """Returns None if bar is valid, else a human-readable rejection reason."""
    if bar.high < bar.low:
        return f"high ({bar.high}) < low ({bar.low})"
    if not (bar.low <= bar.open <= bar.high):
        return f"open ({bar.open}) outside [low, high] ([{bar.low}, {bar.high}])"
    if not (bar.low <= bar.close <= bar.high):
        return f"close ({bar.close}) outside [low, high] ([{bar.low}, {bar.high}])"
    if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
        return "a price is not positive"
    if bar.volume < 0:
        return f"negative volume ({bar.volume})"
    if bar.ts.tzinfo is None:
        return "ts is not timezone-aware"
    if bar.ts.second != 0 or bar.ts.microsecond != 0 or bar.ts.minute % 5 != 0:
        return f"ts ({bar.ts.isoformat()}) is not on the 5-minute grid"
    if not (session_open <= bar.ts < session_close):
        return (f"ts ({bar.ts.isoformat()}) outside session "
                f"[{session_open.isoformat()}, {session_close.isoformat()})")
    return None


def expected_bar_count(session_open: datetime, session_close: datetime) -> int:
    """How many 5-minute bars a full, gapless session should have -- 78 for a normal
    6.5h day, 42 for a 3.5h half day. Used to report gaps, never to fabricate bars."""
    seconds = (session_close - session_open).total_seconds()
    return int(seconds // BAR_SECONDS)


def is_forming(bar: Bar, now: datetime) -> bool:
    """True if bar hasn't fully closed yet as of `now` and must be dropped -- a bar's
    close isn't knowable until bar.ts + 5 minutes has actually elapsed."""
    return bar.ts + timedelta(seconds=BAR_SECONDS) > now


def is_large_jump(prev_close: Decimal, close: Decimal,
                  threshold: Decimal = LARGE_JUMP_THRESHOLD) -> bool:
    """A >25% close-to-close move -- not necessarily wrong (real stocks do gap that
    much on news), so this is a WARNING, never a rejection. prev_close == 0 can't
    happen for a validated bar (validate_bar already rejects non-positive prices), but
    is handled defensively rather than dividing by zero."""
    if prev_close == 0:
        return False
    return abs(close - prev_close) / prev_close > threshold


def detect_close_indexed_days(bars: list[Bar], calendar: str) -> list[date]:
    """A loud, SET-level signal that per-bar validation cannot see (an individual bar
    shifted +5 minutes, as it would be if the provider indexed by bar CLOSE instead of
    bar OPEN, still passes validate_bar on its own -- see
    test_close_indexed_timestamp_cannot_be_detected_by_validation). But the boundary
    bars of a day leak it: if indexing were really close-time, the day's first REAL
    bar would be reported at session_open + 5min (not session_open), and its last
    would be reported at exactly session_close -- which validate_bar correctly rejects
    for being outside [session_open, session_close), but that rejection alone is easy
    to miss amongst routine rejections. This checks the set directly: no bar at
    session_open, but one at exactly session_close, for the same day. Takes the raw
    fetched bars (not just the ones that passed per-bar validation) so the boundary bar
    at session_close -- which validate_bar always rejects -- is still visible here."""
    by_day: dict[date, set[datetime]] = {}
    for bar in bars:
        by_day.setdefault(bar.ts.date(), set()).add(bar.ts)

    suspects = []
    for day, timestamps in by_day.items():
        bounds = session_bounds(day, calendar)
        if bounds is None:
            continue
        session_open, session_close = bounds
        if session_open not in timestamps and session_close in timestamps:
            suspects.append(day)
    return suspects
