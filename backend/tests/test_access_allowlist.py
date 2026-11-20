"""Stage H item 1. Real end-to-end HTTP requests with REAL JWTs (current_user is
NOT dependency-overridden here -- that would bypass the exact allowlist logic
this file proves). Only get_session is overridden, routed to the test transaction
(db_conn, RUN_DB_TESTS=1), same DB-safety pattern as every other route test."""
import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_session
from app.main import app
from tests.conftest import run_db_tests

client = TestClient(app)

SECRET = "test-hs256-secret-at-least-32-bytes-long-xx"
SUPABASE_URL = "https://proj.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"

MINIMAL_RUN_BODY = {
    "instrument_ids": [1],
    "range_start": "2026-12-15T14:30:00+00:00",
    "range_end": "2026-12-15T15:00:00+00:00",
    "currency": "USD",
    "commission_per_order": "1.00",
    "spread_bps": "2",
    "slippage_bps": "1",
}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(get_settings(), "supabase_url", SUPABASE_URL)


def _token(sub: str) -> str:
    now = int(time.time())
    claims = {"sub": sub, "aud": "authenticated", "iss": ISSUER,
             "iat": now, "exp": now + 3600, "email": "t@example.com"}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def _auth_header(sub: str) -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


@pytest.fixture
def _db(db_conn):
    def _fake_session():
        yield db_conn
    app.dependency_overrides[get_session] = _fake_session
    return db_conn


@run_db_tests
def test_user_not_in_allowlist_gets_403_on_get_route(_db, monkeypatch):
    monkeypatch.setattr(get_settings(), "allowed_user_ids", str(uuid4()))  # someone else
    monkeypatch.setattr(get_settings(), "admin_user_ids", "")

    resp = client.get("/instruments", headers=_auth_header(str(uuid4())))

    assert resp.status_code == 403


@run_db_tests
def test_user_not_in_allowlist_gets_403_on_post_route(_db, monkeypatch):
    monkeypatch.setattr(get_settings(), "allowed_user_ids", str(uuid4()))
    monkeypatch.setattr(get_settings(), "admin_user_ids", "")

    resp = client.post("/strategy-runs", json=MINIMAL_RUN_BODY,
                       headers=_auth_header(str(uuid4())))

    assert resp.status_code == 403


@run_db_tests
def test_user_in_allowlist_passes_through(_db, monkeypatch):
    uid = str(uuid4())
    monkeypatch.setattr(get_settings(), "allowed_user_ids", uid)
    monkeypatch.setattr(get_settings(), "admin_user_ids", "")

    resp = client.get("/instruments", headers=_auth_header(uid))

    assert resp.status_code == 200


@run_db_tests
def test_admin_counts_as_allowed_even_if_not_in_allowed_user_ids(_db, monkeypatch):
    uid = str(uuid4())
    monkeypatch.setattr(get_settings(), "allowed_user_ids", str(uuid4()))  # someone else
    monkeypatch.setattr(get_settings(), "admin_user_ids", uid)

    resp = client.get("/instruments", headers=_auth_header(uid))

    assert resp.status_code == 200


@run_db_tests
def test_empty_allowlist_and_empty_admin_list_blocks_everyone(_db, monkeypatch):
    """Fail closed: nobody configured anywhere means nobody gets in, including a
    perfectly valid, correctly-signed JWT."""
    monkeypatch.setattr(get_settings(), "allowed_user_ids", "")
    monkeypatch.setattr(get_settings(), "admin_user_ids", "")

    resp = client.get("/instruments", headers=_auth_header(str(uuid4())))

    assert resp.status_code == 403
