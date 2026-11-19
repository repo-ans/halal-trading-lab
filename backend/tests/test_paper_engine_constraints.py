"""DB-level proof for migration 0005's new CHECK constraints -- these must hold
even if application code has a bug, not just be trusted to app logic."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.market_calendar import session_bar_opens
from app.models.instrument import Instrument
from app.models.paper import PaperFill, PaperOrder
from app.models.strategy import StrategyRun
from tests.conftest import run_db_tests

CAL = "NYSE"
DAY = date(2026, 12, 15)


def _instrument(session, symbol="ZPAPERCK") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
    session.add(inst)
    session.flush()
    return inst


def _run(session, instrument_id: int) -> StrategyRun:
    opens = session_bar_opens(DAY, CAL)
    run = StrategyRun(
        user_id=uuid4(), currency="USD", instrument_ids=[instrument_id],
        range_start=opens[0], range_end=opens[1] + timedelta(minutes=5),
        fast_period=2, slow_period=3, starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), commission_per_order=Decimal("1.00"),
        spread_bps=Decimal("2"), slippage_bps=Decimal("1"), config_hash="x" * 64,
        status="completed", started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


@run_db_tests
def test_filled_order_with_zero_qty_rejected(db_conn):
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    order = PaperOrder(
        strategy_run_id=run.id, signal_id=None, origin="eod_exit", instrument_id=inst.id,
        side="sell", qty=Decimal(0), execution_ts=run.range_start, status="filled",
        rejection_reason=None,
    )
    db_conn.add(order)
    with pytest.raises(IntegrityError):
        db_conn.flush()


@run_db_tests
def test_rejected_order_with_zero_qty_allowed(db_conn):
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    order = PaperOrder(
        strategy_run_id=run.id, signal_id=None, origin="eod_exit", instrument_id=inst.id,
        side="buy", qty=Decimal(0), execution_ts=run.range_start, status="rejected",
        rejection_reason="price_exceeds_max_position",
    )
    db_conn.add(order)
    db_conn.flush()  # no raise


@run_db_tests
def test_unknown_rejection_reason_rejected(db_conn):
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    order = PaperOrder(
        strategy_run_id=run.id, signal_id=None, origin="eod_exit", instrument_id=inst.id,
        side="buy", qty=Decimal(0), execution_ts=run.range_start, status="rejected",
        rejection_reason="not_a_real_reason",
    )
    db_conn.add(order)
    with pytest.raises(IntegrityError):
        db_conn.flush()


@run_db_tests
def test_instrument_frozen_unresolved_eod_reason_allowed(db_conn):
    """The one reason added by migration 0005 specifically for the freeze
    mechanic -- proves it's actually in the enum, not just the Python tuple."""
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    order = PaperOrder(
        strategy_run_id=run.id, signal_id=None, origin="eod_exit", instrument_id=inst.id,
        side="buy", qty=Decimal(0), execution_ts=run.range_start, status="rejected",
        rejection_reason="instrument_frozen_unresolved_eod",
    )
    db_conn.add(order)
    db_conn.flush()  # no raise


@run_db_tests
def test_negative_cash_balance_rejected_at_the_db_level(db_conn):
    """CLAUDE.md section 7 / Stage E plan point 1: cash never goes negative, as a
    hard backstop independent of application logic -- this existed since Stage A
    (ck_no_negative_cash) but had no test proving it until now."""
    inst = _instrument(db_conn)
    run = _run(db_conn, inst.id)
    order = PaperOrder(
        strategy_run_id=run.id, signal_id=None, origin="eod_exit", instrument_id=inst.id,
        side="buy", qty=Decimal(1), execution_ts=run.range_start, status="filled",
        rejection_reason=None,
    )
    db_conn.add(order)
    db_conn.flush()
    fill = PaperFill(
        order_id=order.id, fill_ts=run.range_start, fill_price=Decimal("100"), qty=Decimal(1),
        commission=Decimal("1"), spread_cost=Decimal("0"), slippage_cost=Decimal("0"),
        cash_balance_after=Decimal("-0.01"),
    )
    db_conn.add(fill)
    with pytest.raises(IntegrityError):
        db_conn.flush()
