"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1), auth bypassed via dependency override -- same pattern as
test_instruments_route.py. Any authenticated user may call these routes; the
compliance gate, not an authorization check, decides which instruments get
signals."""
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
COSTS = {"commission_per_order": "1", "spread_bps": "2", "slippage_bps": "1"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def authed(db_conn):
    def _fake_session():
        yield db_conn

    app.dependency_overrides[current_user] = lambda: AuthUser(id=uuid4(), email="t@example.com")
    app.dependency_overrides[get_session] = _fake_session
    return db_conn


def _instrument(session, symbol="ZROUTESTRAT", currency="USD") -> Instrument:
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


def _make_compliant(session, instrument_id: int) -> int:
    check = create_compliance_check(
        session, instrument_id=instrument_id, status="halal", source="manual",
        checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=30),
        created_by=None, evidence_url="https://example.com",
    )
    session.flush()
    return check.id


def _body(instrument_ids, range_start, range_end, currency="USD", **overrides) -> dict:
    body = {
        "instrument_ids": instrument_ids, "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(), "fast_period": 2, "slow_period": 3,
        "currency": currency, **COSTS,
    }
    body.update(overrides)
    return body


# --- auth ---

@run_db_tests
def test_create_requires_jwt():
    resp = client.post("/strategy-runs", json=_body([1], NOW, NOW + timedelta(minutes=5)))
    assert resp.status_code == 401


@run_db_tests
def test_get_run_requires_jwt():
    resp = client.get("/strategy-runs/1")
    assert resp.status_code == 401


# --- happy path ---

@run_db_tests
def test_happy_path_create_get_run_get_signals(authed):
    inst = _instrument(authed)
    _insert_bars(authed, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
    check_id = _make_compliant(authed, inst.id)
    authed.commit()
    opens = session_bar_opens(DAY, CAL)

    resp = client.post("/strategy-runs", json=_body(
        [inst.id], opens[0], opens[9] + timedelta(minutes=5)))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert body["blocked_instruments"] == {}
    assert body["compliance_check_ids"] == {str(inst.id): check_id}
    run_id = body["id"]

    get_resp = client.get(f"/strategy-runs/{run_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["config_hash"] == body["config_hash"]

    signals_resp = client.get(f"/strategy-runs/{run_id}/signals")
    assert signals_resp.status_code == 200
    signals = signals_resp.json()
    assert [s["side"] for s in signals] == ["buy", "sell"]
    # Decimal-as-string, never a JSON float
    assert isinstance(signals[0]["ma_fast"], str)


@run_db_tests
def test_partially_blocked_run_still_completes_for_the_compliant_instrument(authed):
    compliant = _instrument(authed, symbol="ZMIX1")
    blocked = _instrument(authed, symbol="ZMIX2")
    _insert_bars(authed, compliant.id, [10, 10, 10, 10, 20, 30])
    _insert_bars(authed, blocked.id, [10, 10, 10, 10, 20, 30])
    check_id = _make_compliant(authed, compliant.id)
    authed.commit()
    opens = session_bar_opens(DAY, CAL)

    resp = client.post("/strategy-runs", json=_body(
        [compliant.id, blocked.id], opens[0], opens[5] + timedelta(minutes=5)))

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert body["blocked_instruments"] == {str(blocked.id): "no_record"}
    assert body["compliance_check_ids"] == {str(compliant.id): check_id}


@run_db_tests
def test_all_instruments_blocked_returns_422(authed):
    inst = _instrument(authed)
    authed.commit()
    opens = session_bar_opens(DAY, CAL)

    resp = client.post("/strategy-runs", json=_body(
        [inst.id], opens[0], opens[5] + timedelta(minutes=5)))

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "all_instruments_blocked"
    assert resp.json()["detail"]["blocked_instruments"] == {str(inst.id): "no_record"}


@run_db_tests
def test_currency_mismatch_returns_422(authed):
    inst = _instrument(authed, symbol="ZCADROUTE", currency="CAD")
    authed.commit()
    opens = session_bar_opens(DAY, CAL)

    resp = client.post("/strategy-runs", json=_body(
        [inst.id], opens[0], opens[5] + timedelta(minutes=5), currency="USD"))

    assert resp.status_code == 422


@run_db_tests
def test_unknown_instrument_returns_404(authed):
    opens = session_bar_opens(DAY, CAL)
    resp = client.post("/strategy-runs", json=_body(
        [999999], opens[0], opens[5] + timedelta(minutes=5)))
    assert resp.status_code == 404


@run_db_tests
def test_replay_roundtrip_is_byte_identical(authed):
    inst = _instrument(authed)
    _insert_bars(authed, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
    _make_compliant(authed, inst.id)
    authed.commit()
    opens = session_bar_opens(DAY, CAL)

    created = client.post("/strategy-runs", json=_body(
        [inst.id], opens[0], opens[9] + timedelta(minutes=5))).json()

    replay_resp = client.post(f"/strategy-runs/{created['id']}/replay")
    assert replay_resp.status_code == 201
    replay = replay_resp.json()
    assert replay["id"] != created["id"]
    assert replay["config_hash"] == created["config_hash"]

    original_signals = client.get(f"/strategy-runs/{created['id']}/signals").json()
    replay_signals = client.get(f"/strategy-runs/{replay['id']}/signals").json()

    def _strip_instrument_id(rows):
        return [{k: v for k, v in r.items() if k != "instrument_id"} for r in rows]

    assert _strip_instrument_id(replay_signals) == _strip_instrument_id(original_signals)
