"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1), auth bypassed via dependency override -- same pattern as
test_instruments_route.py."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthUser, current_user
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models.instrument import Instrument
from tests.conftest import run_db_tests

client = TestClient(app)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
HEADER = "symbol,exchange,status,source,evidence_url,checked_at,expires_at,notes"
# The route validates checked_at against the REAL wall clock (user-submitted data,
# not a backtest's injected `now`), so test payloads need a genuinely-past
# timestamp -- NOW above is a fixed fictional date and may be in the future
# relative to whenever these tests actually run.
PAST_CHECKED_AT = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


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
def admin(db_conn, monkeypatch):
    """Overrides auth as an ADMIN user and routes DB access to the test transaction."""
    admin_id = uuid4()
    monkeypatch.setattr(get_settings(), "admin_user_ids", str(admin_id))
    app.dependency_overrides[current_user] = lambda: AuthUser(id=admin_id, email="admin@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)
    return db_conn


@pytest.fixture
def non_admin(db_conn, monkeypatch):
    """A real authenticated user who is NOT in ADMIN_USER_IDS."""
    monkeypatch.setattr(get_settings(), "admin_user_ids", str(uuid4()))  # someone else
    app.dependency_overrides[current_user] = lambda: AuthUser(id=uuid4(), email="user@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)
    return db_conn


def _make_instrument(session, symbol="ZCROUTE", exchange="XNAS") -> Instrument:
    inst = Instrument(symbol=symbol, exchange=exchange, currency="USD", name="x")
    session.add(inst)
    session.flush()
    return inst


# --- admin gating ---

@run_db_tests
def test_non_admin_gets_403(non_admin):
    inst = _make_instrument(non_admin)
    non_admin.commit()

    resp = client.post("/compliance", json={
        "instrument_id": inst.id, "status": "halal", "source": "manual",
        "evidence_url": "https://example.com", "checked_at": NOW.isoformat(),
    })

    assert resp.status_code == 403


@run_db_tests
def test_admin_user_ids_unset_means_nobody_is_admin(db_conn, monkeypatch):
    """Fail closed: an empty/unset ADMIN_USER_IDS blocks EVERYONE, including a user
    whose UUID happens to be empty-string-adjacent or otherwise edge-casey."""
    monkeypatch.setattr(get_settings(), "admin_user_ids", "")
    some_user = uuid4()
    app.dependency_overrides[current_user] = lambda: AuthUser(id=some_user, email="x@example.com")
    app.dependency_overrides[get_session] = _fake_session_override(db_conn)
    inst = _make_instrument(db_conn)
    db_conn.commit()

    resp = client.post("/compliance", json={
        "instrument_id": inst.id, "status": "halal", "source": "manual",
        "evidence_url": "https://example.com", "checked_at": NOW.isoformat(),
    })

    assert resp.status_code == 403


@run_db_tests
def test_admin_can_create(admin):
    inst = _make_instrument(admin)
    admin.commit()

    checked_at = PAST_CHECKED_AT
    resp = client.post("/compliance", json={
        "instrument_id": inst.id, "status": "halal", "source": "manual",
        "evidence_url": "https://example.com", "checked_at": checked_at,
    })

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "halal"
    assert body["is_current"] is True
    # default expiry applied (COMPLIANCE_DEFAULT_VALIDITY_DAYS, default 90)
    expires = datetime.fromisoformat(body["expires_at"])
    checked = datetime.fromisoformat(checked_at)
    assert (expires - checked).days in (89, 90)


@run_db_tests
def test_create_rejects_evidence_url_with_no_host(admin):
    """Stage H review finding: a scheme with no host ("https:", "http:///") must
    be rejected by the route, same as any other malformed evidence_url."""
    inst = _make_instrument(admin)
    admin.commit()

    resp = client.post("/compliance", json={
        "instrument_id": inst.id, "status": "halal", "source": "manual",
        "evidence_url": "https:", "checked_at": PAST_CHECKED_AT,
    })

    assert resp.status_code == 422


@run_db_tests
def test_any_authenticated_user_can_read_watchlist(non_admin):
    _make_instrument(non_admin)
    non_admin.commit()

    resp = client.get("/watchlist")

    assert resp.status_code == 200


# --- 409 on conflict ---

@run_db_tests
def test_conflict_returns_409_not_500(admin, monkeypatch):
    import app.routes.compliance as compliance_routes
    from app.services.compliance import ComplianceConflict

    def _raise(*args, **kwargs):
        raise ComplianceConflict()

    monkeypatch.setattr(compliance_routes, "create_compliance_check", _raise)
    inst = _make_instrument(admin)
    admin.commit()

    resp = client.post("/compliance", json={
        "instrument_id": inst.id, "status": "halal", "source": "manual",
        "evidence_url": "https://example.com", "checked_at": PAST_CHECKED_AT,
    })

    assert resp.status_code == 409


# --- CSV dry-run / confirm over real HTTP, multipart file upload ---

@run_db_tests
def test_csv_dry_run_then_confirm_end_to_end(admin):
    inst = _make_instrument(admin, symbol="ZCSVROUTE")
    admin.commit()
    csv_bytes = (
        HEADER + "\nZCSVROUTE,XNAS,halal,manual,https://example.com,2026-11-01,,"
    ).encode("utf-8")

    dry = client.post("/compliance/csv/dry-run",
                      files={"file": ("import.csv", csv_bytes, "text/csv")})
    assert dry.status_code == 200
    assert dry.json()["all_accepted"] is True

    confirm = client.post("/compliance/csv/confirm",
                          files={"file": ("import.csv", csv_bytes, "text/csv")})
    assert confirm.status_code == 200
    assert confirm.json()["applied"] == 1

    watch = client.get("/watchlist")
    row = next(r for r in watch.json() if r["symbol"] == "ZCSVROUTE")
    assert row["status"] == "halal"


@run_db_tests
def test_csv_confirm_rejects_whole_batch_if_any_row_invalid(admin):
    inst = _make_instrument(admin, symbol="ZCSVOK")
    admin.commit()
    csv_bytes = (
        HEADER + "\n"
        "ZCSVOK,XNAS,halal,manual,https://example.com,2026-11-01,,\n"
        "ZCSVGHOST,XNAS,halal,manual,https://example.com,2026-11-01,,"
    ).encode("utf-8")

    confirm = client.post("/compliance/csv/confirm",
                          files={"file": ("import.csv", csv_bytes, "text/csv")})

    assert confirm.status_code == 422
    watch = client.get("/watchlist")
    row = next(r for r in watch.json() if r["symbol"] == "ZCSVOK")
    assert row["status"] is None  # nothing was applied -- true atomicity


@run_db_tests
def test_csv_non_admin_gets_403(non_admin):
    csv_bytes = (HEADER + "\nZX,XNAS,halal,manual,https://example.com,2026-11-01,,").encode()
    resp = client.post("/compliance/csv/dry-run",
                       files={"file": ("import.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 403
