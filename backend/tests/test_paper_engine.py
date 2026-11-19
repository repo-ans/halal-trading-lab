"""Pure engine tests -- no DB, no clock. app/services/paper_engine.py never
touches either, so none of these need the db_test machinery."""
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from app.market_calendar import session_bar_opens
from app.services.paper_engine import SignalIn, _adverse_fill_price, _attempt_buy, simulate_fills

CAL = "NYSE"
SPREAD_BPS = Decimal("2")
SLIPPAGE_BPS = Decimal("1")
COMMISSION = Decimal("1.00")
MAX_POSITION = Decimal("200")
STARTING_CASH = Decimal("1000")


def _kwargs(**overrides) -> dict:
    base = dict(
        commission_per_order=COMMISSION, spread_bps=SPREAD_BPS, slippage_bps=SLIPPAGE_BPS,
        max_position_value=MAX_POSITION, starting_cash=STARTING_CASH,
    )
    base.update(overrides)
    return base


def test_hand_computed_buy_then_sell():
    """raw_open=100 (buy), adjustment=spread/2+slippage=2bps -> fill_price=100.02,
    qty=floor(200/100.02)=1, total_cost=101.02, cash 1000->898.98.
    Later raw_open=110 (sell), fill_price=110*0.9998=109.978, proceeds=108.978,
    cash 898.98->1007.958. Both exact -- no residual rounding at these numbers."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100"), opens[1]: Decimal("110")}
    signals = [
        SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0]),
        SignalIn(signal_id=2, instrument_id=1, side="sell", execution_ts=opens[1]),
    ]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[1] + timedelta(minutes=5), **_kwargs(),
    )

    buy, sell = results
    assert buy.status == "filled"
    assert buy.qty == 1
    assert buy.fill.fill_price == Decimal("100.0200")
    assert buy.fill.spread_cost == Decimal("0.0100")
    assert buy.fill.slippage_cost == Decimal("0.0100")
    assert buy.fill.cash_balance_after == Decimal("898.9800")

    assert sell.status == "filled"
    assert sell.qty == 1
    assert sell.fill.fill_price == Decimal("109.9780")
    assert sell.fill.cash_balance_after == Decimal("1007.9580")


def test_affordability_bug_regression():
    """The bug: an earlier draft rejected a trade whose qty*fill_price+commission
    exactly equals cash, because it wrongly ALSO added spread_cost+slippage_cost
    (already inside fill_price) to the check. cash is set to EXACTLY
    qty*fill_price+commission (101.02) -- the correct check accepts it; the buggy
    one (adding 0.02 more) would have rejected it."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100")}
    signals = [SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0])]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[0] + timedelta(minutes=5),
        **_kwargs(starting_cash=Decimal("101.02")),
    )

    assert results[0].status == "filled"
    assert results[0].fill.cash_balance_after == Decimal("0.0000")


def test_insufficient_cash_rejected_one_cent_short():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100")}
    signals = [SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0])]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[0] + timedelta(minutes=5),
        **_kwargs(starting_cash=Decimal("101.01")),
    )

    assert results[0].status == "rejected"
    assert results[0].rejection_reason == "insufficient_cash"
    assert results[0].qty == 1  # informational: what WOULD have been sized


def test_sizing_uses_fill_price_not_raw_open():
    """raw_open chosen so floor(200/raw_open) = 3 but floor(200/fill_price) = 2 --
    proving the cap is applied to the ADVERSE price, not the raw one."""
    raw_open = Decimal("66.6665")
    fill_price = _adverse_fill_price(raw_open, SPREAD_BPS, SLIPPAGE_BPS, "buy")
    wrong_way = (MAX_POSITION / raw_open).to_integral_value(rounding=ROUND_FLOOR)
    right_way = (MAX_POSITION / fill_price).to_integral_value(rounding=ROUND_FLOOR)
    assert wrong_way == 3
    assert right_way == 2

    ts = session_bar_opens(date(2026, 12, 15), CAL)[0]
    order, _cash = _attempt_buy(
        instrument_id=1, signal_id=1, execution_ts=ts, bars={ts: raw_open},
        cash=STARTING_CASH, has_open_position=False, max_position_value=MAX_POSITION,
        commission_per_order=COMMISSION, spread_bps=SPREAD_BPS, slippage_bps=SLIPPAGE_BPS,
    )
    assert order.qty == 2


def test_price_exceeds_max_position_uses_fill_price_zero_qty():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("250")}
    signals = [SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0])]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[0] + timedelta(minutes=5), **_kwargs(),
    )

    assert results[0].status == "rejected"
    assert results[0].rejection_reason == "price_exceeds_max_position"
    assert results[0].qty == 0


def test_position_already_open_rejects_second_buy():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100"), opens[1]: Decimal("100")}
    signals = [
        SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0]),
        SignalIn(signal_id=2, instrument_id=1, side="buy", execution_ts=opens[1]),
    ]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[1] + timedelta(minutes=5), **_kwargs(),
    )

    assert results[0].status == "filled"
    assert results[1].status == "rejected"
    assert results[1].rejection_reason == "position_already_open"


def test_no_open_position_to_sell_rejects_sell_without_a_position():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100")}
    signals = [SignalIn(signal_id=1, instrument_id=1, side="sell", execution_ts=opens[0])]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[0] + timedelta(minutes=5), **_kwargs(),
    )

    assert results[0].status == "rejected"
    assert results[0].rejection_reason == "no_open_position_to_sell"
    assert results[0].qty == 0


def test_execution_bar_missing_rejects_the_order():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    signals = [SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0])]

    results = simulate_fills(
        signals, {1: {}}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[0] + timedelta(minutes=5), **_kwargs(),
    )

    assert results[0].status == "rejected"
    assert results[0].rejection_reason == "execution_bar_missing"
    assert results[0].intended_price is None


def test_sell_always_closes_the_entire_position_no_resizing():
    """Buy 1 share at 100 (sized to the $200 cap), then sell at a much higher
    price where the cap-based formula would size differently if it were
    (wrongly) applied to the sell too -- the sell must still close exactly the 1
    share actually held, nothing recomputed from max_position_value/price."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100"), opens[1]: Decimal("20")}  # cap/20 would floor to 9, not 1
    signals = [
        SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0]),
        SignalIn(signal_id=2, instrument_id=1, side="sell", execution_ts=opens[1]),
    ]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[1] + timedelta(minutes=5), **_kwargs(),
    )

    assert results[0].qty == 1
    assert results[1].qty == 1  # not 9


def test_eod_exit_closes_an_open_position_at_the_last_bar():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100"), opens[-1]: Decimal("105")}
    signals = [SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0])]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[-1] + timedelta(minutes=5), **_kwargs(),
    )

    eod_orders = [r for r in results if r.origin == "eod_exit"]
    assert len(eod_orders) == 1
    assert eod_orders[0].side == "sell"
    assert eod_orders[0].signal_id is None
    assert eod_orders[0].status == "filled"
    assert eod_orders[0].execution_ts == opens[-1]


def test_no_eod_exit_order_when_nothing_is_open():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    results = simulate_fills(
        [], {1: {}}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[-1] + timedelta(minutes=5), **_kwargs(),
    )
    assert results == []


def test_missing_eod_bar_freezes_the_instrument_for_the_rest_of_the_run():
    """Day 1: buy, then the session's last bar is MISSING -- eod_exit rejected,
    instrument frozen. Day 2: a fresh signal for the SAME instrument must be
    rejected too (instrument_frozen_unresolved_eod), never traded, and must NOT
    generate its own eod_exit (the position was never reopened)."""
    day1, day2 = date(2026, 12, 15), date(2026, 12, 16)
    opens1, opens2 = session_bar_opens(day1, CAL), session_bar_opens(day2, CAL)
    bars = {opens1[0]: Decimal("100")}  # opens1[-1] (session 1's last bar) deliberately absent
    signals = [
        SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens1[0]),
        SignalIn(signal_id=2, instrument_id=1, side="buy", execution_ts=opens2[0]),
    ]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens1[0], range_end=opens2[0] + timedelta(minutes=5), **_kwargs(),
    )

    buy1 = next(r for r in results if r.signal_id == 1)
    eod = next(r for r in results if r.origin == "eod_exit")
    buy2 = next(r for r in results if r.signal_id == 2)

    assert buy1.status == "filled"
    assert eod.status == "rejected" and eod.rejection_reason == "execution_bar_missing_eod"
    assert buy2.status == "rejected" and buy2.rejection_reason == "instrument_frozen_unresolved_eod"
    assert buy2.qty == 0
    # exactly one eod_exit attempt across both sessions -- day 2 never retries
    assert sum(1 for r in results if r.origin == "eod_exit") == 1


def test_ordering_sell_before_buy_frees_cash_for_a_lower_id_buy():
    """Instrument 2 (higher id) opens a position at opens[0], then SELLS it at
    opens[1] -- the same bar instrument 1 (lower id) wants to BUY. Starting cash
    covers instrument 2's opening buy plus instrument 1's later buy, but NOT both
    of instrument 1's and instrument 2's buys at once -- so instrument 1's buy can
    only succeed if instrument 2's same-bar sell is processed first and its
    proceeds land before instrument 1's buy is attempted. Sorting by
    instrument_id alone would try instrument 1 first and fail."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    signals = [
        SignalIn(signal_id=1, instrument_id=2, side="buy", execution_ts=opens[0]),
        SignalIn(signal_id=10, instrument_id=1, side="buy", execution_ts=opens[1]),
        SignalIn(signal_id=20, instrument_id=2, side="sell", execution_ts=opens[1]),
    ]
    bars = {1: {opens[1]: Decimal("100")}, 2: {opens[0]: Decimal("50"), opens[1]: Decimal("50")}}
    # Instrument 2's opening buy: fill_price=50.01, qty=floor(200/50.01)=3,
    # cost=3*50.01+1=151.03 -- leaves only 1.00, nowhere near instrument 1's
    # 101.02 buy, UNLESS instrument 2's same-bar sell (proceeds 148.97) lands first.
    starting_cash = Decimal("152.03")

    results = simulate_fills(
        signals, bars, calendars_by_instrument={1: CAL, 2: CAL},
        range_start=opens[0], range_end=opens[1] + timedelta(minutes=5),
        **_kwargs(starting_cash=starting_cash),
    )

    buy1 = next(r for r in results if r.signal_id == 10)
    sell2 = next(r for r in results if r.signal_id == 20)
    assert sell2.status == "filled"
    assert buy1.status == "filled"


def test_ledger_reconciles_exactly_from_fills_alone():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100"), opens[1]: Decimal("110"), opens[2]: Decimal("90")}
    signals = [
        SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0]),
        SignalIn(signal_id=2, instrument_id=1, side="sell", execution_ts=opens[1]),
        SignalIn(signal_id=3, instrument_id=1, side="buy", execution_ts=opens[2]),
    ]

    results = simulate_fills(
        signals, {1: bars}, calendars_by_instrument={1: CAL},
        range_start=opens[0], range_end=opens[2] + timedelta(minutes=5), **_kwargs(),
    )

    cash = STARTING_CASH
    for r in results:
        assert r.status == "filled"
        movement = r.fill.fill_price * r.fill.qty
        cash = cash - movement - r.fill.commission if r.side == "buy" else cash + movement - r.fill.commission
        assert cash == r.fill.cash_balance_after


def test_determinism_same_inputs_twice():
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, CAL)
    bars = {opens[0]: Decimal("100"), opens[1]: Decimal("110")}
    signals = [
        SignalIn(signal_id=1, instrument_id=1, side="buy", execution_ts=opens[0]),
        SignalIn(signal_id=2, instrument_id=1, side="sell", execution_ts=opens[1]),
    ]
    kwargs = dict(
        calendars_by_instrument={1: CAL}, range_start=opens[0],
        range_end=opens[1] + timedelta(minutes=5), **_kwargs(),
    )

    first = simulate_fills(signals, {1: bars}, **kwargs)
    second = simulate_fills(signals, {1: bars}, **kwargs)
    assert first == second


def test_paper_engine_never_imports_the_compliance_gate():
    """Regression guard (Stage E plan point 11): signals only ever exist for
    instruments that were already compliant when the run started (Stage D) --
    the paper engine must never re-check. Checks actual code references, not
    prose in the module's own docstring explaining why."""
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "app", "services", "paper_engine.py").read_text(encoding="utf-8")
    code_lines = [line for line in source.splitlines() if "compliance" in line.lower()]
    offenders = [line for line in code_lines if "import" in line or "assert_tradable" in line
                or "ComplianceCheck" in line]
    assert offenders == []
