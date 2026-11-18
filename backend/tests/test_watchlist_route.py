"""DB-writing: GET /watchlist's shape and compliance-merge logic. Local throwaway
test database (db_conn, RUN_DB_TESTS=1)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthUser, current_user
from app.db import get_session
from app.main import app
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar
from app.services.compliance import create_compliance_check
from tests.conftest import run_db_tests

client = TestClient(app)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


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


def _make_instrument(session, symbol="ZWATCH") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
    session.add(inst)
    session.flush()
    return inst


@run_db_tests
def test_requires_jwt():
    resp = client.get("/watchlist")
    assert resp.status_code == 401


@run_db_tests
def test_no_compliance_record_shows_no_record(authed):
    _make_instrument(authed)
    authed.commit()

    row = next(r for r in client.get("/watchlist").json() if r["symbol"] == "ZWATCH")

    assert row["status"] is None
    assert row["blocking_reason"] == "no_record"
    assert row["last_checked_at"] is None
    assert row["days_until_expiry"] is None


@run_db_tests
def test_halal_and_valid_shows_null_blocking_reason(authed):
    inst = _make_instrument(authed)
    create_compliance_check(
        authed, instrument_id=inst.id, status="halal", source="manual",
        checked_at=NOW - timedelta(days=10), expires_at=NOW + timedelta(days=80),
        created_by=None, evidence_url="https://example.com",
    )
    authed.commit()

    row = next(r for r in client.get("/watchlist").json() if r["symbol"] == "ZWATCH")

    assert row["status"] == "halal"
    assert row["blocking_reason"] is None
    assert row["days_until_expiry"] is not None
    assert row["days_until_expiry"] > 0


@run_db_tests
def test_expired_shows_expired_blocking_reason(authed):
    inst = _make_instrument(authed)
    create_compliance_check(
        authed, instrument_id=inst.id, status="halal", source="manual",
        checked_at=NOW - timedelta(days=100), expires_at=NOW - timedelta(days=1),
        created_by=None, evidence_url="https://example.com",
    )
    authed.commit()

    row = next(r for r in client.get("/watchlist").json() if r["symbol"] == "ZWATCH")

    assert row["status"] == "halal"  # the record's own status is still shown
    assert row["blocking_reason"] == "expired"


@run_db_tests
def test_not_halal_shows_not_halal_blocking_reason(authed):
    inst = _make_instrument(authed)
    create_compliance_check(
        authed, instrument_id=inst.id, status="not_halal", source="manual",
        checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=89),
        created_by=None,
    )
    authed.commit()

    row = next(r for r in client.get("/watchlist").json() if r["symbol"] == "ZWATCH")

    assert row["status"] == "not_halal"
    assert row["blocking_reason"] == "not_halal"


@run_db_tests
def test_latest_bar_ts_reflects_price_bars(authed):
    inst = _make_instrument(authed)
    bar_ts = datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)
    authed.add(PriceBar(instrument_id=inst.id, ts=bar_ts, interval="5m",
                        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                        close=Decimal("100.5"), volume=1000, source="fake"))
    authed.commit()

    row = next(r for r in client.get("/watchlist").json() if r["symbol"] == "ZWATCH")

    assert row["latest_bar_ts"] == "2026-11-16T14:30:00Z"


@run_db_tests
def test_no_bars_shows_null_latest_bar_ts(authed):
    _make_instrument(authed)
    authed.commit()

    row = next(r for r in client.get("/watchlist").json() if r["symbol"] == "ZWATCH")

    assert row["latest_bar_ts"] is None
