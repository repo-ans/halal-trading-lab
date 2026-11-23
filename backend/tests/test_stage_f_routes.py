"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1), auth bypassed via dependency override -- same pattern as
test_paper_engine_route.py."""
import csv
import io
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


def _instrument(session, symbol="ZSTAGEF") -> Instrument:
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
        session, instrument_id=instrument_id, status="COMPLIANT", source="manual",
        checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=90),
        created_by=None, evidence_url="https://example.com",
    )
    session.flush()


def _create_run(db_conn, owner_id, symbol, closes, max_position_value="200") -> dict:
    inst = _instrument(db_conn, symbol=symbol)
    _insert_bars(db_conn, inst.id, closes)
    _make_compliant(db_conn, inst.id)
    db_conn.commit()
    opens = session_bar_opens(DAY, CAL)

    _authed_as(db_conn, owner_id)
    body = {
        "instrument_ids": [inst.id], "range_start": opens[0].isoformat(),
        "range_end": (opens[len(closes) - 1] + timedelta(minutes=5)).isoformat(),
        "fast_period": 2, "slow_period": 3, "currency": "USD",
        "max_position_value": max_position_value, **COSTS,
    }
    resp = client.post("/strategy-runs", json=body)
    assert resp.status_code == 201
    return resp.json()


# --- GET /strategy-runs (list) ---

@run_db_tests
def test_list_returns_only_own_runs_paginated(db_conn, owner_id):
    other = uuid4()
    _create_run(db_conn, owner_id, "ZLISTA", [10, 10, 10, 10, 20, 30])
    _create_run(db_conn, owner_id, "ZLISTB", [10, 10, 10, 10, 20, 30])
    _create_run(db_conn, other, "ZLISTC", [10, 10, 10, 10, 20, 30])

    _authed_as(db_conn, owner_id)
    full = client.get("/strategy-runs").json()
    assert full["total"] == 2
    assert len(full["items"]) == 2

    page = client.get("/strategy-runs", params={"limit": 1}).json()
    assert page["total"] == 2
    assert len(page["items"]) == 1


# --- GET /strategy-runs/{id}/metrics ---

@run_db_tests
def test_metrics_409_before_paper_run(authed, owner_id):
    run = _create_run(authed, owner_id, "ZMETRICS409", [10, 10, 10, 10, 20, 30])
    resp = client.get(f"/strategy-runs/{run['id']}/metrics")
    assert resp.status_code == 409


@run_db_tests
def test_metrics_happy_path_after_paper_run(authed, owner_id):
    run = _create_run(authed, owner_id, "ZMETRICSOK", [10, 10, 10, 10, 20, 30, 5, 5, 5, 5],
                      max_position_value="1000")
    paper_resp = client.post(f"/strategy-runs/{run['id']}/paper-run")
    assert paper_resp.status_code == 201

    metrics = client.get(f"/strategy-runs/{run['id']}/metrics").json()
    assert metrics["trades_count"] == 1
    assert metrics["realized_pnl"] is not None
    assert metrics["total_fees"] is not None
    assert isinstance(metrics["equity_curve"], list)
    assert len(metrics["equity_curve"]) == 2  # starting point + the one closed trade


# --- GET /strategy-runs/{id}/orders: pagination + filters ---

@run_db_tests
def test_orders_pagination_and_filters(authed, owner_id):
    run = _create_run(authed, owner_id, "ZORDERSPAGE", [10, 10, 10, 10, 20, 30, 5, 5, 5, 5],
                      max_position_value="1000")
    client.post(f"/strategy-runs/{run['id']}/paper-run")

    page = client.get(f"/strategy-runs/{run['id']}/orders", params={"limit": 1}).json()
    assert page["total"] >= 2
    assert len(page["items"]) == 1

    filtered = client.get(f"/strategy-runs/{run['id']}/orders", params={"status": "filled"}).json()
    assert all(o["status"] == "filled" for o in filtered["items"])
    assert filtered["total"] >= 1


# --- CSV export: escaping rules ---

@run_db_tests
def test_csv_exports_escape_hostile_symbol_but_not_numbers(authed, owner_id):
    hostile_symbol = "=1+1"
    run = _create_run(authed, owner_id, hostile_symbol, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5],
                      max_position_value="1000")
    client.post(f"/strategy-runs/{run['id']}/paper-run")

    orders_csv = client.get(f"/strategy-runs/{run['id']}/orders.csv")
    assert orders_csv.status_code == 200
    assert orders_csv.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(orders_csv.text)))
    assert rows, "expected at least one order row"
    assert all(r["instrument_symbol"] == "'" + hostile_symbol for r in rows)
    assert all(r["run_id"] == str(run["id"]) for r in rows)  # run_id column present, numeric, unescaped

    fills_csv = client.get(f"/strategy-runs/{run['id']}/fills.csv")
    assert fills_csv.status_code == 200
    fill_rows = list(csv.DictReader(io.StringIO(fills_csv.text)))
    assert fill_rows, "expected at least one fill row"
    assert all(r["instrument_symbol"] == "'" + hostile_symbol for r in fill_rows)
    # a real negative number (a losing trade's cash delta is still a POSITIVE
    # cash_balance_after here, so assert the general shape instead): numeric
    # cells never start with a stray leading quote.
    for r in fill_rows:
        for col in ("qty", "raw_open", "fill_price", "spread_cost", "slippage_cost",
                   "commission", "cash_balance_after"):
            assert not r[col].startswith("'"), f"{col} was wrongly escaped: {r[col]!r}"


@run_db_tests
def test_csv_negative_number_stays_unescaped(authed, owner_id):
    """A losing round trip can still produce a negative number somewhere a
    future column might carry one -- directly exercise the boundary via
    csv_safety rather than hunting for a naturally-negative fixture value."""
    from app.services.csv_safety import numeric_cell
    assert numeric_cell("-12.3400") == "-12.3400"  # would have been "'-12.3400" under a blanket csv_safe
