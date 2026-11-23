"""DB-backed: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1) -- same pattern as test_compliance_append_only.py."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.market_calendar import session_bar_opens
from app.models.instrument import Instrument
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.models.strategy import Signal, StrategyRun
from app.services.compliance import create_compliance_check
from app.services.strategy_run import (
    LICENCE_RESTRICTED,
    CurrencyMismatch,
    UnknownInstrument,
    replay_strategy_run,
    run_strategy,
)
from tests.conftest import run_db_tests

CAL = "NYSE"
DAY = date(2026, 12, 15)
COSTS = dict(commission_per_order=Decimal("1"), spread_bps=Decimal("2"), slippage_bps=Decimal("1"))


def _instrument(session, symbol="ZSTRAT", currency="USD") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency=currency, name="x")
    session.add(inst)
    session.flush()
    return inst


def _insert_bars(session, instrument_id: int, closes: list[int], source="manual") -> list[datetime]:
    opens = session_bar_opens(DAY, CAL)[:len(closes)]
    session.add_all([
        PriceBar(instrument_id=instrument_id, ts=t, interval=SUPPORTED_INTERVAL,
                 open=Decimal(c), high=Decimal(c), low=Decimal(c), close=Decimal(c),
                 volume=100, source=source)
        for t, c in zip(opens, closes)
    ])
    session.flush()
    return opens


def _compliant(session, instrument_id: int, now: datetime, expires_at: datetime) -> int:
    check = create_compliance_check(
        session, instrument_id=instrument_id, status="COMPLIANT", source="manual",
        checked_at=now - timedelta(days=1), expires_at=expires_at, created_by=None,
        evidence_url="https://example.com",
    )
    session.flush()
    return check.id


@run_db_tests
def test_compliant_instrument_gets_signals_and_snapshots_check_id(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    check_id = _compliant(db_conn, inst.id, now, now + timedelta(days=30))
    db_conn.commit()

    run = run_strategy(
        db_conn, user_id=uuid4(), instrument_ids=[inst.id],
        range_start=session_bar_opens(DAY, CAL)[0],
        range_end=session_bar_opens(DAY, CAL)[9] + timedelta(minutes=5),
        fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), reset_ma_each_session=False, now=now, **COSTS,
    )

    assert run.status == "completed"
    assert run.blocked_instruments == {}
    assert run.compliance_check_ids == {str(inst.id): check_id}
    signals = db_conn.scalars(
        sa.select(Signal).where(Signal.strategy_run_id == run.id)
    ).all()
    assert [s.side for s in signals] == ["buy", "sell"]


@run_db_tests
def test_blocked_instrument_gets_no_signals_and_a_reason(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30])
    db_conn.commit()
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)  # no compliance record at all

    run = run_strategy(
        db_conn, user_id=uuid4(), instrument_ids=[inst.id],
        range_start=session_bar_opens(DAY, CAL)[0],
        range_end=session_bar_opens(DAY, CAL)[5] + timedelta(minutes=5),
        fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), reset_ma_each_session=False, now=now, **COSTS,
    )

    assert run.status == "failed"
    assert run.error == "all_instruments_blocked"
    assert run.blocked_instruments == {str(inst.id): "no_record"}
    assert run.compliance_check_ids == {}
    signal_count = db_conn.scalar(
        sa.select(sa.func.count()).select_from(Signal).where(Signal.strategy_run_id == run.id)
    )
    assert signal_count == 0


@run_db_tests
def test_non_owner_blocked_from_yfinance_sourced_instrument(db_conn):
    """Stage H review finding: the licence guard originally only covered
    GET /instruments/{id}/bars -- a non-owner could still run a full backtest
    against a yfinance-sourced instrument. caller_is_owner=False (the default)
    must block it the same way a missing compliance record does, before any
    signal is generated."""
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30], source="yfinance")
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    _compliant(db_conn, inst.id, now, now + timedelta(days=30))  # compliant AND yfinance
    db_conn.commit()

    run = run_strategy(
        db_conn, user_id=uuid4(), instrument_ids=[inst.id],
        range_start=session_bar_opens(DAY, CAL)[0],
        range_end=session_bar_opens(DAY, CAL)[5] + timedelta(minutes=5),
        fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), reset_ma_each_session=False, now=now,
        caller_is_owner=False, **COSTS,
    )

    assert run.status == "failed"
    assert run.blocked_instruments == {str(inst.id): LICENCE_RESTRICTED}
    assert run.compliance_check_ids == {}
    signal_count = db_conn.scalar(
        sa.select(sa.func.count()).select_from(Signal).where(Signal.strategy_run_id == run.id)
    )
    assert signal_count == 0


@run_db_tests
def test_owner_not_blocked_from_yfinance_sourced_instrument(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5], source="yfinance")
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    check_id = _compliant(db_conn, inst.id, now, now + timedelta(days=30))
    db_conn.commit()

    run = run_strategy(
        db_conn, user_id=uuid4(), instrument_ids=[inst.id],
        range_start=session_bar_opens(DAY, CAL)[0],
        range_end=session_bar_opens(DAY, CAL)[9] + timedelta(minutes=5),
        fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), reset_ma_each_session=False, now=now,
        caller_is_owner=True, **COSTS,
    )

    assert run.status == "completed"
    assert run.blocked_instruments == {}
    assert run.compliance_check_ids == {str(inst.id): check_id}


@run_db_tests
def test_default_caller_is_owner_is_false(db_conn):
    """Fail closed: omitting caller_is_owner entirely must behave exactly like
    passing False, never like "trusted by default"."""
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30], source="yfinance")
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    _compliant(db_conn, inst.id, now, now + timedelta(days=30))
    db_conn.commit()

    run = run_strategy(
        db_conn, user_id=uuid4(), instrument_ids=[inst.id],
        range_start=session_bar_opens(DAY, CAL)[0],
        range_end=session_bar_opens(DAY, CAL)[5] + timedelta(minutes=5),
        fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), reset_ma_each_session=False, now=now, **COSTS,
    )

    assert run.blocked_instruments == {str(inst.id): LICENCE_RESTRICTED}


@run_db_tests
def test_currency_mismatch_rejected(db_conn):
    inst = _instrument(db_conn, symbol="ZSTRATCAD", currency="CAD")
    db_conn.commit()
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(CurrencyMismatch) as exc:
        run_strategy(
            db_conn, user_id=uuid4(), instrument_ids=[inst.id],
            range_start=session_bar_opens(DAY, CAL)[0],
            range_end=session_bar_opens(DAY, CAL)[5] + timedelta(minutes=5),
            fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
            max_position_value=Decimal("200"), reset_ma_each_session=False, now=now, **COSTS,
        )
    assert exc.value.instrument_currency == "CAD"
    assert exc.value.run_currency == "USD"


@run_db_tests
def test_unknown_instrument_rejected(db_conn):
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(UnknownInstrument):
        run_strategy(
            db_conn, user_id=uuid4(), instrument_ids=[999999],
            range_start=session_bar_opens(DAY, CAL)[0],
            range_end=session_bar_opens(DAY, CAL)[5] + timedelta(minutes=5),
            fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
            max_position_value=Decimal("200"), reset_ma_each_session=False, now=now, **COSTS,
        )


@run_db_tests
def test_replay_never_regates_and_stays_byte_identical_after_expiry(db_conn):
    """CLAUDE.md section 7's determinism note: the gate runs once at run start with
    the real clock, and a REPLAY must reuse that snapshot rather than re-evaluating
    the gate -- even once the real record has since expired. t0 is the original
    run's clock (record is valid then); t1 is well after expires_at, used only for
    the replay call. If replay called assert_tradable again, this would raise
    ComplianceBlocked('expired') instead of returning identical signals."""
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
    t0 = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    expires_at = t0 + timedelta(hours=1)
    check_id = _compliant(db_conn, inst.id, t0, expires_at)
    db_conn.commit()

    run_kwargs = dict(
        instrument_ids=[inst.id],
        range_start=session_bar_opens(DAY, CAL)[0],
        range_end=session_bar_opens(DAY, CAL)[9] + timedelta(minutes=5),
        fast_period=2, slow_period=3, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), reset_ma_each_session=False, **COSTS,
    )
    original = run_strategy(db_conn, user_id=uuid4(), now=t0, **run_kwargs)
    assert original.status == "completed"

    t1 = expires_at + timedelta(days=365)  # long after expiry -- irrelevant to replay
    replay = replay_strategy_run(db_conn, original.id, t1)

    assert replay.status == "completed"
    assert replay.id != original.id
    assert replay.config_hash == original.config_hash
    assert replay.compliance_check_ids == original.compliance_check_ids == {str(inst.id): check_id}

    def _signal_tuples(run_id):
        rows = db_conn.scalars(
            sa.select(Signal).where(Signal.strategy_run_id == run_id).order_by(Signal.signal_ts)
        ).all()
        return [(r.instrument_id, r.signal_ts, r.side, r.reason, r.ma_fast, r.ma_slow, r.execution_ts)
                for r in rows]

    assert _signal_tuples(replay.id) == _signal_tuples(original.id)
