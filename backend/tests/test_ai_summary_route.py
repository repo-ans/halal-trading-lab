"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1), auth bypassed via dependency override -- same pattern as
test_stage_f_routes.py."""
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
CLOSES = [10, 10, 10, 10, 20, 30, 5, 5, 5, 5]


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


def _instrument(session, symbol) -> Instrument:
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


def _create_run(db_conn, owner_id, symbol, closes, run_paper=True) -> dict:
    inst = _instrument(db_conn, symbol)
    _insert_bars(db_conn, inst.id, closes)
    _make_compliant(db_conn, inst.id)
    db_conn.commit()
    opens = session_bar_opens(DAY, CAL)

    _authed_as(db_conn, owner_id)
    body = {
        "instrument_ids": [inst.id], "range_start": opens[0].isoformat(),
        "range_end": (opens[len(closes) - 1] + timedelta(minutes=5)).isoformat(),
        "fast_period": 2, "slow_period": 3, "currency": "USD",
        "max_position_value": "1000", **COSTS,
    }
    run = client.post("/strategy-runs", json=body).json()
    if run_paper:
        assert client.post(f"/strategy-runs/{run['id']}/paper-run").status_code == 201
    return run


@run_db_tests
def test_409_before_paper_engine_has_run(authed, owner_id):
    run = _create_run(authed, owner_id, "ZAIROUTE409", CLOSES, run_paper=False)
    resp = client.post(f"/strategy-runs/{run['id']}/ai-summary", json={"mode": "template"})
    assert resp.status_code == 409


@run_db_tests
def test_post_then_get_round_trip(authed, owner_id):
    run = _create_run(authed, owner_id, "ZAIROUTEOK", CLOSES)
    post_resp = client.post(f"/strategy-runs/{run['id']}/ai-summary", json={"mode": "template"})
    assert post_resp.status_code == 201
    body = post_resp.json()
    assert body["mode"] == "template"
    assert body["requested_mode"] == "template"
    assert body["fallback_occurred"] is False
    assert body["heading"] == "Plain-language summary of a simulated result (automatic, no AI)"

    get_resp = client.get(f"/strategy-runs/{run['id']}/ai-summary", params={"mode": "template"})
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == body["id"]


@run_db_tests
def test_get_before_any_post_returns_404(authed, owner_id):
    run = _create_run(authed, owner_id, "ZAIROUTEGET404", CLOSES)
    resp = client.get(f"/strategy-runs/{run['id']}/ai-summary", params={"mode": "llm"})
    assert resp.status_code == 404


@run_db_tests
def test_post_defaults_to_template_mode(authed, owner_id):
    run = _create_run(authed, owner_id, "ZAIROUTEDEFAULT", CLOSES)
    resp = client.post(f"/strategy-runs/{run['id']}/ai-summary", json={})
    assert resp.status_code == 201
    assert resp.json()["mode"] == "template"


@run_db_tests
def test_llm_requested_without_a_key_falls_back_and_reports_it(authed, owner_id):
    run = _create_run(authed, owner_id, "ZAIROUTELLMNOKEY", CLOSES)
    resp = client.post(f"/strategy-runs/{run['id']}/ai-summary", json={"mode": "llm"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["mode"] == "template"
    assert body["requested_mode"] == "llm"
    assert body["fallback_occurred"] is True
    assert body["heading"] == "Plain-language summary of a simulated result (automatic, no AI)"


@run_db_tests
def test_unauthorized_caller_gets_404_on_both_routes(db_conn, owner_id):
    other_user = uuid4()
    run = _create_run(db_conn, owner_id, "ZAIROUTEUNAUTH", CLOSES)
    client.post(f"/strategy-runs/{run['id']}/ai-summary", json={"mode": "template"})

    _authed_as(db_conn, other_user)
    assert client.post(f"/strategy-runs/{run['id']}/ai-summary", json={}).status_code == 404
    assert client.get(f"/strategy-runs/{run['id']}/ai-summary").status_code == 404
