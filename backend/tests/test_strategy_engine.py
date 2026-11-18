"""Pure engine tests -- no DB, no clock. app/services/strategy.py never touches
either, so none of these need the db_test machinery."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.market_calendar import session_bar_opens
from app.services.strategy import STRATEGY_RULES_VERSION, Bar, generate_signals

CAL = "NYSE"


def _bars(opens, closes) -> list[Bar]:
    return [Bar(ts=t, close=Decimal(c)) for t, c in zip(opens, closes)]


def test_strategy_rules_version_is_set():
    assert STRATEGY_RULES_VERSION == "ma-cross-v1"


# --- hand-computed 2/3-period fixture ---
#
# fast=2, slow=3 over closes [10,10,10,10,20,30,5,5,5,5] starting at the session
# open of 2026-12-15 (a plain winter day, 78 bars -- see test_market_calendar.py).
# Traced by hand (window = last 3 closes, fast = last 2 of those):
#   i=2 [10,10,10] fast=20 slow=30  diff=20*3-30*2=0   -> state 0  (first evaluable: sets state, no signal)
#   i=3 [10,10,10] fast=20 slow=30  diff=0              -> state 0  (0->0: not a cross)
#   i=4 [10,10,20] fast=30 slow=40  diff=90-80=10        -> state +1 (0->+1: BUY)
#   i=5 [10,20,30] fast=50 slow=60  diff=150-120=30      -> state +1 (+1->+1: no cross)
#   i=6 [20,30,5]  fast=35 slow=55  diff=105-110=-5       -> state -1 (+1->-1: SELL)
#   i=7 [30,5,5]   fast=10 slow=40  diff=30-80=-50        -> state -1 (-1->-1: no cross)
#   i=8 [5,5,5]    fast=10 slow=15  diff=30-30=0          -> state 0  (-1->0: NOT a cross, equal is never one)
#   i=9 [5,5,5]    fast=10 slow=15  diff=0                -> state 0  (0->0: no cross)
def test_hand_computed_2_3_period_buy_sell_and_ties():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    closes = [10, 10, 10, 10, 20, 30, 5, 5, 5, 5]
    bars = _bars(opens[:len(closes)], closes)

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=bars[0].ts, range_end=bars[-1].ts + timedelta(minutes=5),
    )

    assert [(s.side, s.signal_ts) for s in signals] == [
        ("buy", opens[4]),
        ("sell", opens[6]),
    ]
    buy = signals[0]
    assert buy.execution_ts == opens[5]
    assert buy.ma_fast == Decimal("15.0000")   # 30/2
    assert buy.ma_slow == Decimal("13.3333")   # 40/3, quantized
    sell = signals[1]
    assert sell.execution_ts == opens[7]
    assert sell.ma_fast == Decimal("17.5000")  # 35/2
    assert sell.ma_slow == Decimal("18.3333")  # 55/3, quantized


def test_warm_up_not_enough_bars_yet():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = _bars(opens[:2], [10, 10])  # slow_period=3 needs 3

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[0], range_end=opens[2] + timedelta(minutes=5),
    )
    assert signals == []


def test_first_bar_of_range_gets_a_signal_when_lookback_exists():
    """Same fixture as the hand-computed test, but range_start is pushed to the
    BUY bar itself (index 4) -- bars 0-3 are lookback only. The cross is still
    detected and emitted, proving lookback bars feed the state machine even though
    they're never themselves eligible to emit (point 4, Stage D plan)."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    closes = [10, 10, 10, 10, 20, 30]
    bars = _bars(opens[:len(closes)], closes)

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[4], range_end=opens[5] + timedelta(minutes=5),
    )
    assert len(signals) == 1
    assert signals[0].side == "buy"
    assert signals[0].signal_ts == opens[4]


def test_lookback_only_bars_never_emit_even_if_they_cross():
    """Mirror of the above: the SAME bullish cross at index 4, but range_start is
    now AFTER it -- it must not be emitted, even though it was a real cross."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    closes = [10, 10, 10, 10, 20, 30]
    bars = _bars(opens[:len(closes)], closes)

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[5], range_end=opens[5] + timedelta(minutes=5),
    )
    assert signals == []


def test_gap_aware_skip_does_not_carry_state_forward():
    """closes 0,1,2 = [10,10,20] establish state +1 at i=2 (first evaluable bar --
    sets state, no signal). Bar index 3 is MISSING entirely (a genuine data gap).
    closes 4,5,6 = [30,5,5] would give state -1 at i=6 -- if state had wrongly
    carried forward across the gap (prev=+1), comparing it to -1 looks exactly like
    a SELL cross (prev>=0 and current<0). The correct behaviour (point 2, Stage D
    plan) is that the first evaluable bar after a gap only SETS the state and never
    emits -- so this must produce no signal here. closes 7,8 = [5,30] then
    continues normally and DOES fire a real BUY once state is legitimately
    re-established (window[6,7,8]=[5,5,30]: fast=35,slow=40,diff=105-80=25>0 -> +1,
    vs prev state 0 set at i=7 (window[5,6,7]=[5,5,5]: diff=0) -- proving tracking
    resumes correctly after the gap, it just didn't skip straight to the adversarial
    comparison."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = [
        Bar(ts=opens[0], close=Decimal(10)),
        Bar(ts=opens[1], close=Decimal(10)),
        Bar(ts=opens[2], close=Decimal(20)),
        # opens[3] deliberately missing
        Bar(ts=opens[4], close=Decimal(30)),
        Bar(ts=opens[5], close=Decimal(5)),
        Bar(ts=opens[6], close=Decimal(5)),
        Bar(ts=opens[7], close=Decimal(5)),
        Bar(ts=opens[8], close=Decimal(30)),
    ]

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[0], range_end=opens[8] + timedelta(minutes=5),
    )

    assert [(s.side, s.signal_ts) for s in signals] == [("buy", opens[8])]


def test_last_bar_of_session_discards_signal_regardless_of_side():
    """4 bars at the very end of the 78-bar day: [10,10,10,30]. window@76=[10,10,10]
    sets state 0 (first evaluable). window@77=[10,10,30]: fast=40,slow=50,
    diff=120-100=20>0 -> BUY -- but bar 77 IS the session's last bar, so the next
    expected bar would be a different session. Discarded, not emitted."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = _bars(opens[74:78], [10, 10, 10, 30])

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[74], range_end=opens[77] + timedelta(minutes=5),
    )
    assert signals == []


def test_second_to_last_bar_buy_discarded_sell_kept():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)

    # BUY at the second-to-last bar (index 76): execution bar (77) is the session's
    # last bar -- EOD-exit assumption discards a BUY there.
    buy_bars = _bars(opens[73:77], [10, 10, 10, 30])
    buy_signals = generate_signals(
        buy_bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[73], range_end=opens[76] + timedelta(minutes=5),
    )
    assert buy_signals == []

    # SELL at the same position: kept.
    sell_bars = _bars(opens[73:77], [10, 10, 10, 5])
    sell_signals = generate_signals(
        sell_bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[73], range_end=opens[76] + timedelta(minutes=5),
    )
    assert len(sell_signals) == 1
    assert sell_signals[0].side == "sell"
    assert sell_signals[0].signal_ts == opens[76]
    assert sell_signals[0].execution_ts == opens[77]


def test_half_day_session_length_is_respected_not_hardcoded():
    """2027-11-26 (day after Thanksgiving) is a 42-bar half day, not 78 -- the
    last-bar discard must key off the REAL session length from the calendar."""
    day = date(2027, 11, 26)
    opens = session_bar_opens(day, CAL)
    assert len(opens) == 42
    bars = _bars(opens[38:42], [10, 10, 10, 30])

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[38], range_end=opens[41] + timedelta(minutes=5),
    )
    assert signals == []  # index 41 is this session's last bar


@pytest.mark.parametrize("day, next_session_day", [
    (date(2026, 11, 13), date(2026, 11, 16)),  # weekend
    (date(2027, 11, 24), date(2027, 11, 26)),  # Thanksgiving holiday
])
def test_session_boundary_never_crossed_by_execution_ts(day, next_session_day):
    """A signal at the session's last bar must not execute at the NEXT session's
    open, however many calendar days away that is. range_end spans past the next
    session so `expected` actually contains that next bar -- proving the discard is
    an explicit rule, not just an accident of the range stopping short."""
    opens = session_bar_opens(day, CAL)
    next_opens = session_bar_opens(next_session_day, CAL)
    bars = _bars(opens[-4:], [10, 10, 10, 30])

    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens[-4], range_end=next_opens[0] + timedelta(minutes=5),
    )
    assert signals == []


def test_reset_ma_each_session_no_signals_in_first_slow_period_bars_and_never_spans_sessions():
    """reset_ma_each_session=True: the slow_period window must lie entirely within
    one session. day1's last 3 bars ([10,10,30], a real cross) plus day2's first 2
    bars must NOT combine into a cross at day2's first bar (that would require a
    window spanning the two sessions); day2's own first slow_period bars can't
    signal either, since there isn't yet a full in-session window."""
    day1 = date(2026, 12, 15)
    day2 = date(2026, 12, 16)
    opens1 = session_bar_opens(day1, CAL)
    opens2 = session_bar_opens(day2, CAL)

    bars = (
        _bars(opens1[-3:], [10, 10, 30])
        + _bars(opens2[:3], [5, 5, 5])
    )
    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=bars[0].ts, range_end=opens2[3] + timedelta(minutes=5),
        reset_ma_each_session=True,
    )
    assert signals == []


def test_default_mode_ma_window_spans_session_boundary():
    """reset_ma_each_session=False (the default): the MA window is allowed to mix
    day1's last bars with day2's first bars. This is the WINDOW spanning a
    boundary (allowed by default) -- not to be confused with execution_ts crossing
    a boundary (never allowed, see test_session_boundary_never_crossed_by_execution_ts)."""
    day1 = date(2026, 12, 15)
    day2 = date(2026, 12, 16)
    opens1 = session_bar_opens(day1, CAL)
    opens2 = session_bar_opens(day2, CAL)

    # window@opens1[-1] (day1's last bar) = [10,10,10] -> state 0 (first evaluable,
    # sets the baseline). window@opens2[0] (day2's first bar) =
    # [opens1[-2], opens1[-1], opens2[0]] = [10,10,30] -> fast=40,slow=50,
    # diff=120-100=20>0 -> state +1: a real BUY, only detectable if the window was
    # allowed to reach back across the session boundary into day1.
    bars = _bars(opens1[-3:], [10, 10, 10]) + _bars(opens2[:1], [30])
    signals = generate_signals(
        bars, fast_period=2, slow_period=3, calendar=CAL,
        range_start=opens1[-3], range_end=opens2[0] + timedelta(minutes=5),
        reset_ma_each_session=False,
    )
    assert [(s.side, s.signal_ts) for s in signals] == [("buy", opens2[0])]


def _synthetic_series(day: date, calendar: str, n: int) -> list[Bar]:
    opens = session_bar_opens(day, calendar)[:n]
    closes = []
    price = Decimal(100)
    for i in range(n):
        # a few deliberate ramps and dips so 5/20 actually crosses more than once
        if i % 15 < 7:
            price += Decimal("1.5")
        else:
            price -= Decimal("1.5")
        closes.append(price)
    return _bars(opens, closes)


def test_prefix_stability_no_look_ahead():
    day = date(2026, 12, 15)
    full = _synthetic_series(day, CAL, 60)
    range_start, range_end = full[0].ts, full[-1].ts + timedelta(minutes=5)
    full_signals = generate_signals(full, fast_period=5, slow_period=20, calendar=CAL,
                                    range_start=range_start, range_end=range_end)
    assert full_signals, "fixture should produce at least one signal to be a meaningful test"

    for k in range(21, len(full) + 1):
        prefix = full[:k]
        prefix_end = prefix[-1].ts + timedelta(minutes=5)
        prefix_signals = generate_signals(prefix, fast_period=5, slow_period=20, calendar=CAL,
                                          range_start=range_start, range_end=prefix_end)
        expected = [s for s in full_signals if s.signal_ts < prefix_end]
        assert prefix_signals == expected, f"prefix k={k} diverged"


def test_perturbation_altering_future_bars_never_changes_past_signals():
    day = date(2026, 12, 15)
    full = _synthetic_series(day, CAL, 40)
    range_start, range_end = full[0].ts, full[-1].ts + timedelta(minutes=5)
    baseline = generate_signals(full, fast_period=5, slow_period=20, calendar=CAL,
                                range_start=range_start, range_end=range_end)

    cutoff = full[25].ts
    mutated = [Bar(ts=b.ts, close=b.close * 3 + 7) if b.ts > cutoff else b for b in full]
    mutated_signals = generate_signals(mutated, fast_period=5, slow_period=20, calendar=CAL,
                                       range_start=range_start, range_end=range_end)

    before = [s for s in baseline if s.signal_ts <= cutoff]
    mutated_before = [s for s in mutated_signals if s.signal_ts <= cutoff]
    assert mutated_before == before


def test_perturbation_deleting_future_bars_never_changes_past_signals():
    day = date(2026, 12, 15)
    full = _synthetic_series(day, CAL, 40)
    range_start, range_end = full[0].ts, full[-1].ts + timedelta(minutes=5)
    baseline = generate_signals(full, fast_period=5, slow_period=20, calendar=CAL,
                                range_start=range_start, range_end=range_end)

    cutoff = full[25].ts
    truncated = [b for b in full if b.ts <= cutoff]
    truncated_signals = generate_signals(truncated, fast_period=5, slow_period=20, calendar=CAL,
                                         range_start=range_start, range_end=range_end)

    before = [s for s in baseline if s.signal_ts <= cutoff]
    assert truncated_signals == before


def test_determinism_same_inputs_twice():
    day = date(2026, 12, 15)
    bars = _synthetic_series(day, CAL, 40)
    range_start, range_end = bars[0].ts, bars[-1].ts + timedelta(minutes=5)
    kwargs = dict(fast_period=5, slow_period=20, calendar=CAL,
                  range_start=range_start, range_end=range_end)

    first = generate_signals(bars, **kwargs)
    second = generate_signals(bars, **kwargs)
    assert first == second
