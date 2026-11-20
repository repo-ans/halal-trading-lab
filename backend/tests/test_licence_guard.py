"""Stage H item 2 (CLAUDE.md section 3's licence guard). DB-writing: real inserts
against the test transaction (db_conn, RUN_DB_TESTS=1). Auth is dependency
-overridden here (not real JWTs) -- the allowlist/JWT machinery is tested
separately in test_access_allowlist.py / test_jwt_security.py; this file is only
about is_owner()'s own effect, which depends solely on AuthUser.id."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthUser, current_user
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar
from tests.conftest import run_db_tests

client = TestClient(app)

BAR_TS = datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_session, None)


def _fake_session_override(db_conn):
    def _fake_session():
        yield db_conn
    return _fake_session


@pytest.fixture
def owner(db_conn, monkeypatch):
    owner_id = uuid4()
    monkeypatch.setattr(get_settings(), "owner_user_ids", str(owner_id))
    app.dependency_overrides[current_user] = lambda: AuthUser(id=owner_id, email="o@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)
    return db_conn


@pytest.fixture
def non_owner(db_conn, monkeypatch):
    monkeypatch.setattr(get_settings(), "owner_user_ids", str(uuid4()))  # someone else
    app.dependency_overrides[current_user] = lambda: AuthUser(id=uuid4(), email="u@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)
    return db_conn


def _bar(instrument_id: int, ts: datetime, source: str) -> PriceBar:
    return PriceBar(instrument_id=instrument_id, ts=ts, interval="5m",
                    open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                    close=Decimal("100.5"), volume=1000, source=source)


def _make_instrument(session, symbol="ZLICENCE") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
    session.add(inst)
    session.flush()
    return inst


def _get_bars(inst_id: int, span=timedelta(hours=1)):
    return client.get(f"/instruments/{inst_id}/bars", params={
        "from": int(BAR_TS.timestamp()), "to": int((BAR_TS + span).timestamp()),
    })


@run_db_tests
def test_owner_sees_yfinance_bars(owner):
    inst = _make_instrument(owner)
    owner.add(_bar(inst.id, BAR_TS, "yfinance"))
    owner.commit()

    resp = _get_bars(inst.id)

    assert resp.status_code == 200
    assert len(resp.json()) == 1


@run_db_tests
def test_allowed_non_owner_gets_503_for_yfinance_bars(non_owner):
    inst = _make_instrument(non_owner)
    non_owner.add(_bar(inst.id, BAR_TS, "yfinance"))
    non_owner.commit()

    resp = _get_bars(inst.id)

    assert resp.status_code == 503
    assert "yfinance" in resp.json()["detail"].lower() or "licensed" in resp.json()["detail"].lower()


@run_db_tests
def test_non_owner_still_sees_non_yfinance_bars(non_owner):
    inst = _make_instrument(non_owner)
    non_owner.add(_bar(inst.id, BAR_TS, "manual"))
    non_owner.commit()

    resp = _get_bars(inst.id)

    assert resp.status_code == 200
    assert len(resp.json()) == 1


@run_db_tests
def test_mixed_sources_are_blocked_for_non_owner_even_if_only_one_row_is_yfinance(non_owner):
    inst = _make_instrument(non_owner)
    non_owner.add(_bar(inst.id, BAR_TS, "manual"))
    non_owner.add(_bar(inst.id, BAR_TS + timedelta(minutes=5), "yfinance"))
    non_owner.commit()

    resp = _get_bars(inst.id)

    assert resp.status_code == 503


@run_db_tests
def test_empty_owner_user_ids_blocks_everyone_including_the_real_owner(db_conn, monkeypatch):
    """Fail closed, same shape as ADMIN_USER_IDS: an unset OWNER_USER_IDS means
    nobody -- not even whoever would eventually be the real owner -- is owner."""
    monkeypatch.setattr(get_settings(), "owner_user_ids", "")
    app.dependency_overrides[current_user] = lambda: AuthUser(id=uuid4(), email="x@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)
    inst = _make_instrument(db_conn)
    db_conn.add(_bar(inst.id, BAR_TS, "yfinance"))
    db_conn.commit()

    resp = _get_bars(inst.id)

    assert resp.status_code == 503
