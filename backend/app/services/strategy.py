"""The 5/20 (configurable) moving-average-cross signal engine (CLAUDE.md section 7).

Pure function: no DB, no clock, no compliance gate, no bypass of any kind. The
caller (app/services/strategy_run.py) is the only thing that ever calls the gate,
once, at run start, with the real clock -- this module only ever sees bars that are
already known to belong to a compliant instrument.

UNCONFIRMED ASSUMPTIONS (owner has not confirmed; recorded here and in CLAUDE.md
section 7 so they're visible wherever results get questioned):
 1. MA continues across sessions by default. reset_ma_each_session=True instead
    requires the whole slow_period window to lie within one session.
 2. EOD exit is modelled as happening at the OPEN of the session's last bar: a BUY
    signal whose execution bar would be that last bar is discarded outright (no time
    to hold before the forced exit); a SELL signal there is kept.
 3. Gap-aware, and state does NOT carry forward across a gap: a bar is evaluable
    only if every bar its slow_period window needs is present in the data. After any
    non-evaluable bar, the next evaluable bar only sets the tracked cross-state --
    it never emits a signal, even if comparing it to the state from before the gap
    would otherwise look like a cross. The state from before the gap is discarded,
    not reused.
 4. Equal fast/slow MA is never a cross (state 0 satisfies neither "> 0" nor "< 0").

SESSION BOUNDARY (a correctness fix, not a configurable assumption):
execution_ts is always the next calendar-expected bar open in the SAME trading
session as the signal bar -- never the next session's open. Concretely:
  - if the signal bar is the session's last bar, the next expected bar is next
    session's first bar (a different session) -- the signal is discarded, both
    sides. Otherwise a signal near the close would execute as an overnight
    position, and this app is day-trading only (CLAUDE.md section 7).
  - this is independent of range_end: a signal is never discarded just because its
    execution bar falls outside [range_start, range_end) or has no data yet --
    deciding whether a fill is possible is Stage E's job, not this engine's, and
    discarding on range_end would break prefix-stability (the no-look-ahead
    must-pass test).

MA comparisons never divide: the cross sign is sign(fast_sum*slow_period -
slow_sum*fast_period), which has the same sign as (ma_fast - ma_slow) since both
periods are positive, but is exact -- no rounding error can flip a near-tie.
ma_fast/ma_slow in the returned SignalResult are the quantized (NUMERIC(18,4))
values, for display/storage only; they are never what decided the cross.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.market_calendar import session_bar_opens, trading_sessions

STRATEGY_RULES_VERSION = "ma-cross-v1"  # bump whenever the signal rules change (CLAUDE.md)

_QUANT = Decimal("0.0001")


@dataclass(frozen=True)
class Bar:
    ts: datetime  # bar open, UTC
    close: Decimal


@dataclass(frozen=True)
class SignalResult:
    signal_ts: datetime
    side: str  # "buy" | "sell"
    reason: str
    ma_fast: Decimal
    ma_slow: Decimal
    execution_ts: datetime


def _session_schedule(calendar: str, start_day, end_day, interval_minutes: int) -> list[list[datetime]]:
    sessions = []
    for day in trading_sessions(calendar, start_day, end_day):
        opens = session_bar_opens(day, calendar, interval_minutes)
        if opens:
            sessions.append(opens)
    return sessions


def _window_start_index(i: int, pos: int, slow_period: int, reset_ma_each_session: bool) -> int | None:
    """The index into the flat `expected` list where the slow_period-bar window
    ending at index i begins, or None if there isn't enough history yet -- globally
    (non-reset mode) or within the current session (reset mode)."""
    if reset_ma_each_session:
        if pos < slow_period - 1:
            return None
        return i - slow_period + 1
    if i < slow_period - 1:
        return None
    return i - slow_period + 1


def _cross_sign(fast_sum: Decimal, slow_sum: Decimal, fast_period: int, slow_period: int) -> int:
    diff = fast_sum * slow_period - slow_sum * fast_period
    if diff > 0:
        return 1
    if diff < 0:
        return -1
    return 0


def generate_signals(
    bars: list[Bar],
    *,
    fast_period: int,
    slow_period: int,
    calendar: str,
    range_start: datetime,
    range_end: datetime,
    interval_minutes: int = 5,
    reset_ma_each_session: bool = False,
) -> list[SignalResult]:
    """`bars` is ascending and may extend before range_start (lookback/warm-up,
    loaded by the caller) -- a cross is tracked across those bars but only ever
    EMITTED as a signal if signal_ts falls inside [range_start, range_end). Bars at
    or after range_end are not expected to be present; if the caller supplies any,
    they're used like any other bar (this function doesn't look at "now")."""
    if not bars:
        return []

    with localcontext() as ctx:
        ctx.prec = 40  # explicit -- never inherits whatever the ambient context is

        schedule_start = bars[0].ts.date()
        sessions = _session_schedule(calendar, schedule_start, range_end.date(), interval_minutes)

        expected: list[tuple[datetime, int, int]] = []  # (ts, pos_in_session, session_len)
        for session in sessions:
            session_len = len(session)
            for pos, ts in enumerate(session):
                expected.append((ts, pos, session_len))

        bars_by_ts = {b.ts: b for b in bars}

        results: list[SignalResult] = []
        have_prior_state = False
        prev_state = 0

        for i, (ts, pos, session_len) in enumerate(expected):
            start = _window_start_index(i, pos, slow_period, reset_ma_each_session)
            window_ts = None
            if start is not None:
                candidate = [expected[j][0] for j in range(start, i + 1)]
                if all(t in bars_by_ts for t in candidate):
                    window_ts = candidate

            if window_ts is None:
                have_prior_state = False
                continue

            fast_sum = sum((bars_by_ts[t].close for t in window_ts[-fast_period:]), Decimal(0))
            slow_sum = sum((bars_by_ts[t].close for t in window_ts), Decimal(0))
            current_state = _cross_sign(fast_sum, slow_sum, fast_period, slow_period)

            if not have_prior_state:
                prev_state = current_state
                have_prior_state = True
                continue

            side = None
            if prev_state <= 0 and current_state > 0:
                side = "buy"
            elif prev_state >= 0 and current_state < 0:
                side = "sell"

            prev_state = current_state  # always advances, cross or not

            if side is None:
                continue
            if pos == session_len - 1:
                continue  # signal bar is the session's last bar -- next expected
                          # bar is a different session (session-boundary rule)

            execution_ts = expected[i + 1][0]
            if pos + 1 == session_len - 1 and side == "buy":
                continue  # BUY whose execution bar is the session's last bar -- EOD-exit assumption 2

            if not (range_start <= ts < range_end):
                continue  # lookback-only bar: state tracked, nothing emitted

            ma_fast = (fast_sum / Decimal(fast_period)).quantize(_QUANT, rounding=ROUND_HALF_UP)
            ma_slow = (slow_sum / Decimal(slow_period)).quantize(_QUANT, rounding=ROUND_HALF_UP)
            reason = f"MA{fast_period} crossed {'above' if side == 'buy' else 'below'} MA{slow_period}"
            results.append(SignalResult(
                signal_ts=ts, side=side, reason=reason,
                ma_fast=ma_fast, ma_slow=ma_slow, execution_ts=execution_ts,
            ))

        return results
