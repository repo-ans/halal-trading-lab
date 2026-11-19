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
COSTS = {"commission_per_order": "1.00", "spread_bps": "2", "slippage_bps": "1"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_session, None)


def _authed_as(db_conn, user_id):
    def _fake_session():
        yield db_conn

    app.dependency_overrides[current_user] = lambda: AuthUser(id=user_id, email="t@example.com")
    app.dependency_overrides[get_session] = _fake_session


@pytest.fixture
def owner_id():
    return uuid4()


@pytest.fixture
def authed(db_conn, owner_id):
    _authed_as(db_conn, owner_id)
    return db_conn


def _instrument(session, symbol="ZPAPERROUTE") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
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
        session, instrument_id=instrument_id, status="halal", source="manual",
        checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=90),
        created_by=None, evidence_url="https://example.com",
    )
    session.flush()


def _create_run(db_conn, owner_id, closes) -> dict:
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, closes)
    _make_compliant(db_conn, inst.id)
    db_conn.commit()
    opens = session_bar_opens(DAY, CAL)

    _authed_as(db_conn, owner_id)
    body = {
        "instrument_ids": [inst.id], "range_start": opens[0].isoformat(),
        "range_end": (opens[len(closes) - 1] + timedelta(minutes=5)).isoformat(),
        "fast_period": 2, "slow_period": 3, "currency": "USD", **COSTS,
    }
    resp = client.post("/strategy-runs", json=body)
    assert resp.status_code == 201
    return resp.json()


@run_db_tests
def test_paper_run_happy_path_then_orders_and_fills(authed, owner_id):
    run = _create_run(authed, owner_id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])

    resp = client.post(f"/strategy-runs/{run['id']}/paper-run")
    assert resp.status_code == 201
    body = resp.json()
    assert body["paper_status"] == "completed"
    assert body["order_count"] == 2
    assert body["fill_count"] == 2

    orders = client.get(f"/strategy-runs/{run['id']}/orders").json()
    assert [o["side"] for o in orders] == ["buy", "sell"]
    assert isinstance(orders[0]["qty"], str)  # Decimal-as-string, never a JSON float

    fills = client.get(f"/strategy-runs/{run['id']}/fills").json()
    assert len(fills) == 2
    assert isinstance(fills[0]["fill_price"], str)


@run_db_tests
def test_paper_run_second_call_returns_409(authed, owner_id):
    run = _create_run(authed, owner_id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
    first = client.post(f"/strategy-runs/{run['id']}/paper-run")
    assert first.status_code == 201

    second = client.post(f"/strategy-runs/{run['id']}/paper-run")
    assert second.status_code == 409


@run_db_tests
def test_unauthorized_caller_gets_404_on_every_route(db_conn, owner_id):
    other_user = uuid4()
    run = _create_run(db_conn, owner_id, [10, 10, 10, 10, 20, 30])

    _authed_as(db_conn, other_user)
    assert client.get(f"/strategy-runs/{run['id']}").status_code == 404
    assert client.get(f"/strategy-runs/{run['id']}/signals").status_code == 404
    assert client.post(f"/strategy-runs/{run['id']}/replay").status_code == 404
    assert client.post(f"/strategy-runs/{run['id']}/paper-run").status_code == 404
    assert client.get(f"/strategy-runs/{run['id']}/orders").status_code == 404
    assert client.get(f"/strategy-runs/{run['id']}/fills").status_code == 404

    # the owner can still reach all of them -- proves the 404s above are about
    # ownership, not a broken run
    _authed_as(db_conn, owner_id)
    assert client.get(f"/strategy-runs/{run['id']}").status_code == 200
    assert client.get(f"/strategy-runs/{run['id']}/signals").status_code == 200
