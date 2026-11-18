"""DB-writing: real inserts + real queries against the local test database
(db_conn, RUN_DB_TESTS=1). current_user and get_session are dependency-overridden --
auth bypassed with a fake user, DB routed to the test transaction, same pattern as
test_jobs_route.py and test_backfill_service.py."""
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
from tests.conftest import run_db_tests

client = TestClient(app)

NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
BAR_TS = datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def authed(db_conn):
    """Overrides auth (fake user, no real JWT) and DB (the test transaction). The
    override for get_session must itself be a generator function -- FastAPI decides
    whether to advance a dependency with next()/yield based on inspecting the
    CALLABLE (inspect.isgeneratorfunction), not its return value, so a lambda
    returning an iterator is treated as an ordinary callable and the raw iterator
    object gets passed straight into the route instead of the session it yields."""
    def _fake_session():
        yield db_conn

    app.dependency_overrides[current_user] = lambda: AuthUser(id=uuid4(), email="t@example.com")
    app.dependency_overrides[get_session] = _fake_session
    return db_conn


def _make_instrument(session, symbol="ZROUTE", exchange="XNAS", currency="USD") -> Instrument:
    inst = Instrument(symbol=symbol, exchange=exchange, currency=currency, name="x")
    session.add(inst)
    session.flush()
    return inst


def _make_bar(instrument_id: int, ts: datetime, close="100.5") -> PriceBar:
    return PriceBar(instrument_id=instrument_id, ts=ts, interval="5m",
                    open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                    close=Decimal(close), volume=1000, source="fake")


# --- auth ---

@run_db_tests
def test_instruments_requires_jwt():
    resp = client.get("/instruments")
    assert resp.status_code == 401


@run_db_tests
def test_bars_requires_jwt():
    resp = client.get("/instruments/1/bars", params={"from": 0, "to": 1})
    assert resp.status_code == 401


# --- GET /instruments ---

@run_db_tests
def test_instruments_list_returns_latest_ts_bar_count_and_timezone(authed):
    inst = _make_instrument(authed)
    authed.add(_make_bar(inst.id, BAR_TS))
    authed.add(_make_bar(inst.id, BAR_TS + timedelta(minutes=5)))
    authed.commit()

    resp = client.get("/instruments")

    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["symbol"] == "ZROUTE")
    assert row["exchange"] == "XNAS"
    assert row["currency"] == "USD"
    assert row["timezone"] == "America/New_York"
    assert row["bar_count"] == 2
    assert row["latest_bar_ts"] == "2026-11-16T14:35:00Z"
    assert row["compliance"] is None


@run_db_tests
def test_instruments_tsx_timezone(authed):
    _make_instrument(authed, symbol="ZTSX", exchange="XTSE", currency="CAD")
    authed.commit()

    resp = client.get("/instruments")

    row = next(r for r in resp.json() if r["symbol"] == "ZTSX")
    assert row["timezone"] == "America/Toronto"


@run_db_tests
def test_instruments_with_no_bars_has_null_latest_ts_and_zero_count(authed):
    _make_instrument(authed, symbol="ZEMPTY")
    authed.commit()

    resp = client.get("/instruments")

    row = next(r for r in resp.json() if r["symbol"] == "ZEMPTY")
    assert row["latest_bar_ts"] is None
    assert row["bar_count"] == 0


# --- GET /instruments/{id}/bars ---

@run_db_tests
def test_bars_returned_ascending_by_ts(authed):
    inst = _make_instrument(authed)
    authed.add(_make_bar(inst.id, BAR_TS + timedelta(minutes=10)))
    authed.add(_make_bar(inst.id, BAR_TS))
    authed.add(_make_bar(inst.id, BAR_TS + timedelta(minutes=5)))
    authed.commit()

    resp = client.get(f"/instruments/{inst.id}/bars", params={
        "from": int(BAR_TS.timestamp()), "to": int((BAR_TS + timedelta(hours=1)).timestamp()),
    })

    assert resp.status_code == 200
    ts_values = [b["ts"] for b in resp.json()]
    assert ts_values == sorted(ts_values)
    assert len(ts_values) == 3


@run_db_tests
def test_bars_prices_are_strings_not_floats(authed):
    inst = _make_instrument(authed)
    authed.add(_make_bar(inst.id, BAR_TS, close="334.5411"))
    authed.commit()

    resp = client.get(f"/instruments/{inst.id}/bars", params={
        "from": int(BAR_TS.timestamp()), "to": int((BAR_TS + timedelta(minutes=5)).timestamp()),
    })

    bar = resp.json()[0]
    assert isinstance(bar["close"], str)
    assert bar["close"] == "334.5411"
    assert isinstance(bar["open"], str)
    assert isinstance(bar["ts"], int)
    assert bar["ts"] == int(BAR_TS.timestamp())


@run_db_tests
def test_bars_from_after_to_returns_422(authed):
    inst = _make_instrument(authed)
    authed.commit()

    resp = client.get(f"/instruments/{inst.id}/bars",
                      params={"from": 1000, "to": 500})

    assert resp.status_code == 422


@run_db_tests
def test_bars_unsupported_interval_returns_422(authed):
    inst = _make_instrument(authed)
    authed.commit()

    resp = client.get(f"/instruments/{inst.id}/bars",
                      params={"from": 0, "to": 1000, "interval": "1h"})

    assert resp.status_code == 422


@run_db_tests
def test_bars_unknown_instrument_returns_404(authed):
    resp = client.get("/instruments/999999/bars", params={"from": 0, "to": 1000})
    assert resp.status_code == 404


@run_db_tests
def test_bars_exceeding_5000_cap_returns_422(authed, monkeypatch):
    import app.routes.instruments as routes_instruments
    monkeypatch.setattr(routes_instruments, "MAX_BARS_PER_REQUEST", 1)

    inst = _make_instrument(authed)
    authed.add(_make_bar(inst.id, BAR_TS))
    authed.add(_make_bar(inst.id, BAR_TS + timedelta(minutes=5)))
    authed.commit()

    resp = client.get(f"/instruments/{inst.id}/bars", params={
        "from": int(BAR_TS.timestamp()), "to": int((BAR_TS + timedelta(hours=1)).timestamp()),
    })

    assert resp.status_code == 422
    assert "cap" in resp.json()["detail"]
