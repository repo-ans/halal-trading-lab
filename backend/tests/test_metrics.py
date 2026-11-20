"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1). Builds paper_orders/paper_fills directly (not via the full
strategy-run/paper-engine pipeline) so each fixture is a minimal, controlled
case for the metrics math itself."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.market_calendar import session_bar_opens
from app.models.instrument import Instrument
from app.models.paper import PaperFill, PaperOrder
from app.models.strategy import StrategyRun
from app.services.metrics import compute_metrics
from tests.conftest import run_db_tests

CAL = "NYSE"
RANGE_START = session_bar_opens(date(2026, 12, 15), CAL)[0]


def _instrument(session, symbol="ZMETRICS") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
    session.add(inst)
    session.flush()
    return inst


def _run(session, instrument_id: int, starting_cash=Decimal("1000")) -> StrategyRun:
    run = StrategyRun(
        user_id=uuid4(), currency="USD", instrument_ids=[instrument_id],
        range_start=RANGE_START, range_end=RANGE_START + timedelta(hours=6),
        fast_period=5, slow_period=20, starting_cash=starting_cash,
        max_position_value=Decimal("1000"), commission_per_order=Decimal("1.00"),
        spread_bps=Decimal("2"), slippage_bps=Decimal("1"), config_hash="x" * 64,
        status="completed", started_at=datetime.now(timezone.utc), paper_status="completed",
    )
    session.add(run)
    session.flush()
    return run


def _fill(session, run, instrument_id, side, qty, ts, fill_price, cash_after,
         commission=Decimal("1"), spread_cost=Decimal("0"), slippage_cost=Decimal("0"),
         origin="eod_exit") -> None:
    # origin defaults to "eod_exit", not "signal" -- these fixtures never attach
    # a real Signal row (signal_id stays None), and ck_order_origin_signal
    # requires signal_id IS NOT NULL whenever origin='signal'.
    order = PaperOrder(
        strategy_run_id=run.id, signal_id=None, origin=origin, instrument_id=instrument_id,
        side=side, qty=qty, intended_price=fill_price, execution_ts=ts, status="filled",
        rejection_reason=None,
    )
    session.add(order)
    session.flush()
    session.add(PaperFill(
        order_id=order.id, fill_ts=ts, fill_price=fill_price, qty=qty, commission=commission,
        spread_cost=spread_cost, slippage_cost=slippage_cost, cash_balance_after=cash_after,
    ))
    session.flush()


@run_db_tests
def test_one_win_one_loss(db_conn):
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    t0 = RANGE_START
    # win: buy 100, sell 110, qty 1, commission 1 each -> pnl = 10 - 1 - 1 = 8
    _fill(db_conn, run, inst.id, "buy", Decimal(1), t0, Decimal("100"), Decimal("898.00"))
    _fill(db_conn, run, inst.id, "sell", Decimal(1), t0 + timedelta(minutes=5), Decimal("110"),
         Decimal("1007.00"))
    # loss: buy 100, sell 90, qty 1 -> pnl = -10 - 1 - 1 = -12
    _fill(db_conn, run, inst.id, "buy", Decimal(1), t0 + timedelta(minutes=10), Decimal("100"),
         Decimal("906.00"))
    _fill(db_conn, run, inst.id, "sell", Decimal(1), t0 + timedelta(minutes=15), Decimal("90"),
         Decimal("995.00"))
    db_conn.commit()

    m = compute_metrics(db_conn, run)

    assert m.trades_count == 2
    assert m.realized_pnl == Decimal("-4.0000")  # 8 + (-12)
    assert m.total_return == (Decimal("-4") / Decimal("1000")).quantize(Decimal("0.0001"))
    assert m.win_rate == Decimal("0.5000")
    assert m.avg_win == Decimal("8.0000")
    assert m.avg_non_win == Decimal("-12.0000")
    assert m.total_fees == Decimal("4.0000")  # 4 fills * $1 commission
    assert m.unresolved_positions == 0


@run_db_tests
def test_zero_orders_metrics_are_null_not_strings(db_conn):
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    db_conn.commit()

    m = compute_metrics(db_conn, run)

    assert m.trades_count == 0
    assert m.realized_pnl is None
    assert m.total_return is None
    assert m.win_rate is None
    assert m.avg_win is None
    assert m.avg_non_win is None
    assert m.total_fees == Decimal("0.0000")
    assert m.max_drawdown == Decimal("0.0000")
    assert m.unresolved_positions == 0
    assert m.equity_curve == [(run.range_start, run.starting_cash)]


@run_db_tests
def test_unresolved_position_excluded_never_counted_as_a_loss(db_conn):
    """A buy with no matching sell (the only way Stage E produces this: a failed
    EOD exit). Must not appear as a trade, a loss, or a point on the curve --
    only as an unresolved-position count."""
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    t0 = RANGE_START
    _fill(db_conn, run, inst.id, "buy", Decimal(1), t0, Decimal("100"), Decimal("899.00"))
    db_conn.commit()

    m = compute_metrics(db_conn, run)

    assert m.trades_count == 0
    assert m.realized_pnl is None  # NOT a loss -- undefined, because there are no closed trades
    assert m.unresolved_positions == 1
    assert m.unresolved_instrument_ids == [inst.id]
    assert m.equity_curve == [(run.range_start, run.starting_cash)]  # no point for the open buy
    # the buy's own fee is still real money spent, regardless of resolution
    assert m.total_fees == Decimal("1.0000")


@run_db_tests
def test_win_rate_defined_with_only_losses_avg_win_still_null(db_conn):
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    t0 = RANGE_START
    _fill(db_conn, run, inst.id, "buy", Decimal(1), t0, Decimal("100"), Decimal("899.00"))
    _fill(db_conn, run, inst.id, "sell", Decimal(1), t0 + timedelta(minutes=5), Decimal("90"),
         Decimal("988.00"))
    db_conn.commit()

    m = compute_metrics(db_conn, run)

    assert m.trades_count == 1
    assert m.win_rate == Decimal("0.0000")  # defined: zero wins out of one closed trade
    assert m.avg_win is None  # no winning trades to average
    assert m.avg_non_win == Decimal("-12.0000")


@run_db_tests
def test_equity_curve_has_no_point_at_the_buy(db_conn):
    """The actual bug this design avoids: a cash-ledger curve would show the
    cost of a 2-share buy as an instant ~70% drawdown before anything is lost.
    The realized curve only moves on the closing sell, by that trade's P&L."""
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id, starting_cash=Decimal("1000"))
    t0 = RANGE_START
    # buy costs 298 (a would-be ~70% drawdown if plotted) -- sell for a small loss
    _fill(db_conn, run, inst.id, "buy", Decimal(2), t0, Decimal("149"), Decimal("702.00"))
    _fill(db_conn, run, inst.id, "sell", Decimal(2), t0 + timedelta(minutes=5), Decimal("148"),
         Decimal("998.00"))
    db_conn.commit()

    m = compute_metrics(db_conn, run)

    assert len(m.equity_curve) == 2  # starting point + the one sell -- never a point at the buy
    assert m.equity_curve[0] == (run.range_start, Decimal("1000"))
    # pnl = (148-149)*2 - 1 - 1 = -4 -> 1000 - 4 = 996, not anywhere near 702
    assert m.equity_curve[1][1] == Decimal("996.0000")
    assert m.max_drawdown == Decimal("0.0040")  # 4/1000, not ~70%
