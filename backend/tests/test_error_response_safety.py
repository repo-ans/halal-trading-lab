"""Stage H item 5. Forces a genuine unhandled exception (no custom exception
handler is registered in app/main.py -- confirmed by reading it) and asserts the
response body never contains a traceback, a file path, SQL text, or the
exception's own message. `raise_server_exceptions=False` is required so the
TestClient returns the response instead of re-raising the exception into the
test process itself."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthUser, current_user
from app.db import get_session
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

SECRET_LOOKING_TEXT = "password='hunter2' SELECT * FROM price_bars"


def _raising_session():
    raise RuntimeError(SECRET_LOOKING_TEXT)
    yield  # pragma: no cover -- unreachable; keeps this a generator function


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_session, None)


def test_unhandled_exception_returns_500_with_no_leaked_detail():
    app.dependency_overrides[current_user] = lambda: AuthUser(id=uuid4(), email="t@example.com")
    app.dependency_overrides[get_session] = _raising_session

    resp = client.get("/instruments")

    assert resp.status_code == 500
    body = resp.text
    for leak in (SECRET_LOOKING_TEXT, "RuntimeError", "Traceback", ".py", "hunter2",
                "SELECT", "password"):
        assert leak not in body, f"response body leaked {leak!r}: {body!r}"
