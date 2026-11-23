"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1), auth bypassed via dependency override -- same pattern as
test_strategy_runs_route.py."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthUser, current_user
from app.db import get_session
from app.main import app
from app.market_calendar import session_bar_opens
from app.models.instrument import Instrument
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.services.compliance import create_compliance_check
from tests.conftest import run_db_tests

client = TestClient(app)

CAL = "NYSE"
DAY = date(2026, 12, 15)
NOW = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def authed(db_conn):
    def _fake_session():
        yield db_conn

    user_id = uuid4()
    app.dependency_overrides[current_user] = lambda: AuthUser(id=user_id, email="t@example.com")
    app.dependency_overrides[get_session] = _fake_session
    return db_conn


def _instrument(session, symbol="ZROUTEREPLAY", currency="USD") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency=currency, name="x")
    session.add(inst)
    session.flush()
    return inst


def _insert_bars(session, instrument_id: int, closes: list[int]) -> list[datetime]:
    opens = session_bar_opens(DAY, CAL)[:len(closes)]
    session.add_all([
        PriceBar(instrument_id=instrument_id, ts=t, interval=SUPPORTED_INTERVAL,
                 open=Decimal(c), high=Decimal(c), low=Decimal(c), close=Decimal(c),
                 volume=100, source="manual")
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


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@run_db_tests
def test_create_session_and_place_buy_then_sell(authed):
    inst = _instrument(authed)
    opens = _insert_bars(authed, inst.id, [10, 20, 30])
    _make_compliant(authed, inst.id)
    authed.commit()

    resp = client.post("/replay-sessions", json={
        "instrument_id": inst.id, "session_day": DAY.isoformat(),
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    session_id = body["session"]["id"]
    assert body["position_qty"] == "0"
    assert body["cash_balance"] == "1000.0000"

    buy = client.post(f"/replay-sessions/{session_id}/orders", json={
        "side": "buy", "clicked_bar_ts": _iso(opens[0]),
    })
    assert buy.status_code == 201, buy.text
    assert buy.json()["position_qty"] != "0"

    sell = client.post(f"/replay-sessions/{session_id}/orders", json={
        "side": "sell", "clicked_bar_ts": _iso(opens[1]),
    })
    assert sell.status_code == 201, sell.text
    assert sell.json()["position_qty"] == "0"

    fills = client.get(f"/replay-sessions/{session_id}/fills")
    assert fills.status_code == 200
    assert len(fills.json()) == 2
    assert fills.json()[1]["realized_pnl"] is not None


@run_db_tests
def test_rejected_order_returns_structured_422(authed):
    inst = _instrument(authed)
    opens = _insert_bars(authed, inst.id, [10, 20])
    _make_compliant(authed, inst.id)
    authed.commit()

    resp = client.post("/replay-sessions", json={
        "instrument_id": inst.id, "session_day": DAY.isoformat(),
    })
    session_id = resp.json()["session"]["id"]

    rejected = client.post(f"/replay-sessions/{session_id}/orders", json={
        "side": "sell", "clicked_bar_ts": _iso(opens[0]),
    })
    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert detail["error"] == "order_rejected"
    assert detail["reason"] == "no_open_position_to_sell"
    assert "message" in detail


@run_db_tests
def test_end_session_auto_flats_and_marks_completed(authed):
    inst = _instrument(authed)
    opens = _insert_bars(authed, inst.id, [10, 20, 30])
    # The EOD exit fills at the REAL session's last bar (78 bars/day on NYSE),
    # not the 3rd of the 3 bars this test happened to insert.
    real_last_bar = session_bar_opens(DAY, CAL)[-1]
    authed.add(PriceBar(instrument_id=inst.id, ts=real_last_bar, interval=SUPPORTED_INTERVAL,
                        open=Decimal(25), high=Decimal(25), low=Decimal(25), close=Decimal(25),
                        volume=100, source="manual"))
    _make_compliant(authed, inst.id)
    authed.commit()

    session_id = client.post("/replay-sessions", json={
        "instrument_id": inst.id, "session_day": DAY.isoformat(),
    }).json()["session"]["id"]
    client.post(f"/replay-sessions/{session_id}/orders", json={
        "side": "buy", "clicked_bar_ts": _iso(opens[0]),
    })

    ended = client.post(f"/replay-sessions/{session_id}/end")
    assert ended.status_code == 200, ended.text
    assert ended.json()["session"]["status"] == "completed"
    assert ended.json()["position_qty"] == "0"


@run_db_tests
def test_session_not_found_for_a_different_user(authed):
    inst = _instrument(authed)
    _insert_bars(authed, inst.id, [10, 20])
    _make_compliant(authed, inst.id)
    authed.commit()

    session_id = client.post("/replay-sessions", json={
        "instrument_id": inst.id, "session_day": DAY.isoformat(),
    }).json()["session"]["id"]

    app.dependency_overrides[current_user] = lambda: AuthUser(id=uuid4(), email="other@example.com")
    resp = client.get(f"/replay-sessions/{session_id}")
    assert resp.status_code == 404


@run_db_tests
def test_create_session_blocked_without_compliance_record(authed):
    inst = _instrument(authed)
    _insert_bars(authed, inst.id, [10, 20])
    authed.commit()

    resp = client.post("/replay-sessions", json={
        "instrument_id": inst.id, "session_day": DAY.isoformat(),
    })
    assert resp.status_code == 422
    assert "no_record" in resp.json()["detail"]
