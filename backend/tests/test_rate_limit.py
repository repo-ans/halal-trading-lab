"""Stage H item 4. Pure unit tests on SlidingWindowLimiter (no DB, no clock reads --
`now` is always an explicit parameter), plus a couple of route-level HTTP tests
proving POST /strategy-runs and POST .../paper-run actually call it."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.routes.strategy_runs as routes_strategy_runs
from app.auth import AuthUser, current_user
from app.db import get_session
from app.main import app
from app.market_calendar import session_bar_opens
from app.models.instrument import Instrument
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.rate_limit import SlidingWindowLimiter
from app.services.compliance import create_compliance_check
from tests.conftest import run_db_tests

client = TestClient(app)
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

# --- pure unit tests ---


def test_allows_up_to_the_limit_then_blocks():
    limiter = SlidingWindowLimiter(max_requests=3, window=timedelta(seconds=60))
    for _ in range(3):
        limiter.check("user-a", T0)
    with pytest.raises(HTTPException) as exc:
        limiter.check("user-a", T0)
    assert exc.value.status_code == 429


def test_different_keys_are_independent():
    limiter = SlidingWindowLimiter(max_requests=1, window=timedelta(seconds=60))
    limiter.check("user-a", T0)
    limiter.check("user-b", T0)  # must not raise -- separate key, separate budget


def test_old_hits_roll_off_the_window():
    limiter = SlidingWindowLimiter(max_requests=1, window=timedelta(seconds=60))
    limiter.check("user-a", T0)
    with pytest.raises(HTTPException):
        limiter.check("user-a", T0 + timedelta(seconds=30))  # still inside the window
    limiter.check("user-a", T0 + timedelta(seconds=61))  # now outside it -- allowed again


# --- route-level: prove the routes actually call the limiter ---


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_session, None)


def _fake_session_override(db_conn):
    def _fake_session():
        yield db_conn
    return _fake_session


MINIMAL_RUN_BODY = {
    "instrument_ids": [999999],  # need not exist -- the limiter runs before any lookup
    "range_start": "2026-12-15T14:30:00+00:00",
    "range_end": "2026-12-15T15:00:00+00:00",
    "currency": "USD",
    "commission_per_order": "1.00",
    "spread_bps": "2",
    "slippage_bps": "1",
}


@run_db_tests
def test_create_run_route_is_rate_limited(db_conn, monkeypatch):
    tiny = SlidingWindowLimiter(max_requests=1, window=timedelta(seconds=60))
    monkeypatch.setattr(routes_strategy_runs, "STRATEGY_RUN_LIMITER", tiny)
    user_id = uuid4()
    app.dependency_overrides[current_user] = lambda: AuthUser(id=user_id, email="t@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)

    first = client.post("/strategy-runs", json=MINIMAL_RUN_BODY)
    second = client.post("/strategy-runs", json=MINIMAL_RUN_BODY)

    assert first.status_code == 404  # unknown instrument -- got past the limiter fine
    assert second.status_code == 429


@run_db_tests
def test_paper_run_route_is_rate_limited(db_conn, monkeypatch):
    tiny = SlidingWindowLimiter(max_requests=1, window=timedelta(seconds=60))
    monkeypatch.setattr(routes_strategy_runs, "PAPER_RUN_LIMITER", tiny)
    user_id = uuid4()
    app.dependency_overrides[current_user] = lambda: AuthUser(id=user_id, email="t@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)

    inst = Instrument(symbol="ZRATELIMIT", exchange="XNAS", currency="USD", name="x")
    db_conn.add(inst)
    db_conn.flush()
    opens = session_bar_opens(T0.date(), "NYSE")
    for t, c in zip(opens[:3], [10, 10, 10]):
        db_conn.add(PriceBar(instrument_id=inst.id, ts=t, interval=SUPPORTED_INTERVAL,
                             open=Decimal(c), high=Decimal(c), low=Decimal(c), close=Decimal(c),
                             volume=100, source="manual"))
    create_compliance_check(
        db_conn, instrument_id=inst.id, status="COMPLIANT", source="manual",
        checked_at=T0 - timedelta(days=1), expires_at=T0 + timedelta(days=90),
        created_by=None, evidence_url="https://example.com",
    )
    db_conn.commit()

    body = {
        "instrument_ids": [inst.id], "range_start": opens[0].isoformat(),
        "range_end": (opens[2] + timedelta(minutes=5)).isoformat(),
        "fast_period": 2, "slow_period": 3, "currency": "USD",
        "max_position_value": "1000", "commission_per_order": "1.00",
        "spread_bps": "2", "slippage_bps": "1",
    }
    run = client.post("/strategy-runs", json=body).json()

    first = client.post(f"/strategy-runs/{run['id']}/paper-run")
    second = client.post(f"/strategy-runs/{run['id']}/paper-run")

    assert first.status_code in (201, 409)  # got past the limiter either way
    assert second.status_code == 429
