"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1) -- same pattern as test_paper_engine_service.py."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.market_calendar import session_bar_opens
from app.models.instrument import Instrument
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.services.compliance import ComplianceBlocked, create_compliance_check
from app.services.replay_engine import (
    LicenceRestricted,
    NotATradingSession,
    OrderRejected,
    ReplaySessionNotActive,
    create_replay_session,
    end_replay_session,
    get_position,
    place_order,
)
from tests.conftest import run_db_tests

CAL = "NYSE"
DAY = date(2026, 12, 15)
NOW = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
COSTS = dict(commission_per_order=Decimal("1.00"), spread_bps=Decimal("2"), slippage_bps=Decimal("1"))


def _instrument(session, symbol="ZREPLAY", currency="USD") -> Instrument:
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


def _make_compliant(session, instrument_id: int) -> None:
    create_compliance_check(
        session, instrument_id=instrument_id, status="COMPLIANT", source="manual",
        checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=30),
        created_by=None, evidence_url="https://example.com",
    )
    session.flush()


def _new_session(db_conn, closes, **overrides):
    inst = _instrument(db_conn)
    opens = _insert_bars(db_conn, inst.id, closes)
    _make_compliant(db_conn, inst.id)
    db_conn.commit()
    kwargs = dict(
        user_id=uuid4(), instrument_id=inst.id, session_day=DAY,
        starting_cash=Decimal("1000"), max_position_value=Decimal("200"), now=NOW, **COSTS,
    )
    kwargs.update(overrides)
    rs = create_replay_session(db_conn, **kwargs)
    return rs, inst, opens


@run_db_tests
def test_create_session_happy_path(db_conn):
    rs, _inst, _opens = _new_session(db_conn, [10, 10])
    assert rs.status == "active"
    assert rs.compliance_check_id is not None
    position = get_position(db_conn, rs)
    assert position.qty == 0
    assert position.entry_price is None
    assert position.cash_balance == Decimal("1000")


@run_db_tests
def test_create_session_blocked_without_compliance_record(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10])
    db_conn.commit()
    with pytest.raises(ComplianceBlocked):
        create_replay_session(
            db_conn, user_id=uuid4(), instrument_id=inst.id, session_day=DAY,
            starting_cash=Decimal("1000"), max_position_value=Decimal("200"), now=NOW, **COSTS,
        )


@run_db_tests
def test_create_session_blocked_for_yfinance_data_non_owner(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10], source="yfinance")
    _make_compliant(db_conn, inst.id)
    db_conn.commit()
    with pytest.raises(LicenceRestricted):
        create_replay_session(
            db_conn, user_id=uuid4(), instrument_id=inst.id, session_day=DAY,
            starting_cash=Decimal("1000"), max_position_value=Decimal("200"), now=NOW,
            caller_is_owner=False, **COSTS,
        )
    # The owner is unaffected by the same data.
    rs = create_replay_session(
        db_conn, user_id=uuid4(), instrument_id=inst.id, session_day=DAY,
        starting_cash=Decimal("1000"), max_position_value=Decimal("200"), now=NOW,
        caller_is_owner=True, **COSTS,
    )
    assert rs.status == "active"


@run_db_tests
def test_create_session_rejects_non_trading_day(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10])
    _make_compliant(db_conn, inst.id)
    db_conn.commit()
    saturday = date(2026, 12, 19)
    with pytest.raises(NotATradingSession):
        create_replay_session(
            db_conn, user_id=uuid4(), instrument_id=inst.id, session_day=saturday,
            starting_cash=Decimal("1000"), max_position_value=Decimal("200"), now=NOW, **COSTS,
        )


@run_db_tests
def test_buy_fills_at_next_bar_open_not_clicked_bars_own_close(db_conn):
    """No-look-ahead (CLAUDE.md section 7, must-pass test 4): a click while
    looking at bar 0 must fill at bar 1's open, never bar 0's own price."""
    rs, inst, opens = _new_session(db_conn, [10, 20, 30])

    fill = place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[0])

    assert fill.fill_ts == opens[1]  # the NEXT bar, not opens[0]
    # raw_open=20, adjustment_bps = 2/2 + 1 = 2 -> 20 * 1.0002 = 20.004
    assert fill.fill_price == Decimal("20.0040")
    assert fill.qty == Decimal(9)  # floor(200 / 20.004) = 9
    assert fill.cash_balance_after == Decimal("1000") - (Decimal("20.0040") * 9 + Decimal("1.00"))


@run_db_tests
def test_sell_closes_entire_position_and_computes_realized_pnl(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 20, 30, 40])
    buy = place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[0])  # fills at opens[1]=20
    sell = place_order(db_conn, rs.id, rs.user_id, side="sell", clicked_bar_ts=opens[1])  # fills at opens[2]=30

    assert sell.fill_ts == opens[2]
    expected_pnl = (sell.fill_price - buy.fill_price) * buy.qty - buy.commission - sell.commission
    assert sell.realized_pnl == expected_pnl

    position = get_position(db_conn, rs)
    assert position.qty == 0
    assert position.entry_price is None
    assert position.cash_balance == sell.cash_balance_after


@run_db_tests
def test_buy_while_position_open_is_rejected(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 20, 30])
    place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[0])
    with pytest.raises(OrderRejected) as exc:
        place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[1])
    assert exc.value.reason == "position_already_open"


@run_db_tests
def test_sell_while_flat_is_rejected(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 20])
    with pytest.raises(OrderRejected) as exc:
        place_order(db_conn, rs.id, rs.user_id, side="sell", clicked_bar_ts=opens[0])
    assert exc.value.reason == "no_open_position_to_sell"


@run_db_tests
def test_clicking_at_the_sessions_last_bar_is_rejected(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 20])
    # opens is truncated to the 2 bars inserted; the session's REAL last bar
    # (per the full NYSE calendar, 78 bars/day) is further out than that --
    # clicking there has no next bar at all within the session.
    real_last_bar = session_bar_opens(DAY, CAL)[-1]
    with pytest.raises(OrderRejected) as exc:
        place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=real_last_bar)
    assert exc.value.reason == "no_next_bar_in_session"


@run_db_tests
def test_buy_rejected_when_price_exceeds_max_position(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 1000], max_position_value=Decimal("200"))
    with pytest.raises(OrderRejected) as exc:
        place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[0])
    assert exc.value.reason == "price_exceeds_max_position"


@run_db_tests
def test_buy_rejected_when_cash_insufficient(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 15], starting_cash=Decimal("5"))
    with pytest.raises(OrderRejected) as exc:
        place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[0])
    assert exc.value.reason == "insufficient_cash"


@run_db_tests
def test_execution_bar_missing_is_rejected_not_invented(db_conn):
    inst = _instrument(db_conn)
    opens = session_bar_opens(DAY, CAL)
    # Only bar 0 exists -- bar 1 (the would-be execution bar) has no price_bars row.
    db_conn.add(PriceBar(instrument_id=inst.id, ts=opens[0], interval=SUPPORTED_INTERVAL,
                        open=Decimal(10), high=Decimal(10), low=Decimal(10), close=Decimal(10),
                        volume=100, source="manual"))
    _make_compliant(db_conn, inst.id)
    db_conn.commit()
    rs = create_replay_session(
        db_conn, user_id=uuid4(), instrument_id=inst.id, session_day=DAY,
        starting_cash=Decimal("1000"), max_position_value=Decimal("200"), now=NOW, **COSTS,
    )
    with pytest.raises(OrderRejected) as exc:
        place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[0])
    assert exc.value.reason == "execution_bar_missing"


@run_db_tests
def test_end_session_auto_flats_open_position(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 20, 30])
    place_order(db_conn, rs.id, rs.user_id, side="buy", clicked_bar_ts=opens[0])  # fills at opens[1]=20
    # The EOD exit fills at the REAL session's last bar (78 bars/day on NYSE),
    # not the 3rd of the 3 bars this test happened to insert -- give that real
    # last bar a price too, or the exit has nothing to fill at.
    real_last_bar = session_bar_opens(DAY, CAL)[-1]
    db_conn.add(PriceBar(instrument_id=inst.id, ts=real_last_bar, interval=SUPPORTED_INTERVAL,
                        open=Decimal(25), high=Decimal(25), low=Decimal(25), close=Decimal(25),
                        volume=100, source="manual"))
    db_conn.commit()

    ended = end_replay_session(db_conn, rs.id, rs.user_id, NOW + timedelta(hours=1))

    assert ended.status == "completed"
    position = get_position(db_conn, ended)
    assert position.qty == 0  # auto-flatted at the session's real last bar's open


@run_db_tests
def test_end_session_is_a_noop_on_an_already_flat_session(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 20])
    ended = end_replay_session(db_conn, rs.id, rs.user_id, NOW + timedelta(hours=1))
    assert ended.status == "completed"
    position = get_position(db_conn, ended)
    assert position.cash_balance == Decimal("1000")


@run_db_tests
def test_ending_an_already_completed_session_raises(db_conn):
    rs, inst, opens = _new_session(db_conn, [10, 20])
    end_replay_session(db_conn, rs.id, rs.user_id, NOW + timedelta(hours=1))
    with pytest.raises(ReplaySessionNotActive):
        end_replay_session(db_conn, rs.id, rs.user_id, NOW + timedelta(hours=2))
