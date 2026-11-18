"""No DB, no network: the backfill runner is always dependency-overridden here.
TestClient is used WITHOUT the `with` context manager so lifespan never runs --
nothing this route needs comes from lifespan, and running it would start a real
scheduler and fire a real (network/DB-touching) backfill attempt."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, get_backfill_runner
from app.services.backfill import BackfillReport, GapDay, InstrumentReport

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_backfill_runner, None)


@pytest.fixture
def job_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "job_secret", "supersecret")


def test_job_secret_unset_returns_503(monkeypatch):
    monkeypatch.setattr(get_settings(), "job_secret", "")
    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "anything"})
    assert resp.status_code == 503


def test_missing_header_returns_401(job_secret):
    resp = client.post("/jobs/backfill")
    assert resp.status_code == 401


def test_wrong_secret_returns_401(job_secret):
    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "wrong"})
    assert resp.status_code == 401


def test_correct_secret_runs_backfill_and_returns_report(job_secret):
    report = BackfillReport(instruments=[
        InstrumentReport(symbol="AAPL", exchange="XNAS", fetched=5, inserted=3),
    ])
    app.dependency_overrides[get_backfill_runner] = lambda: (lambda: report)

    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "supersecret"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["instruments"][0]["symbol"] == "AAPL"
    assert body["instruments"][0]["fetched"] == 5
    assert body["instruments"][0]["inserted"] == 3


def test_any_instrument_error_returns_502(job_secret):
    """502, not 207: 207 is a 2xx, and curl --fail / GitHub Actions treat any 2xx as
    success, so a cron would stay green through a real upstream failure."""
    report = BackfillReport(instruments=[
        InstrumentReport(symbol="AAPL", exchange="XNAS", fetched=5, inserted=5),
        InstrumentReport(symbol="RY", exchange="XTSE", error="simulated upstream failure"),
    ])
    app.dependency_overrides[get_backfill_runner] = lambda: (lambda: report)

    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "supersecret"})

    assert resp.status_code == 502
    body = resp.json()
    assert body["instruments"][1]["error"] == "simulated upstream failure"


def test_large_recent_session_gap_returns_502_even_without_a_fetch_error(job_secret):
    report = BackfillReport(instruments=[
        InstrumentReport(symbol="AAPL", exchange="XNAS", fetched=10, inserted=10,
                         recent_session_gap_pct=Decimal("50")),  # > 10% threshold
    ])
    app.dependency_overrides[get_backfill_runner] = lambda: (lambda: report)

    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "supersecret"})

    assert resp.status_code == 502


def test_small_recent_session_gap_stays_200(job_secret):
    report = BackfillReport(instruments=[
        InstrumentReport(symbol="AAPL", exchange="XNAS", fetched=75, inserted=75,
                         recent_session_gap_pct=Decimal("3.8")),  # <= 10% threshold
    ])
    app.dependency_overrides[get_backfill_runner] = lambda: (lambda: report)

    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "supersecret"})

    assert resp.status_code == 200


def test_gap_in_older_not_most_recent_session_does_not_fail_the_cron(job_secret):
    """Gaps in historical sessions are permanent -- recent_session_gap_pct reflects
    only the most recent COMPLETED session (see backfill.py), so an old gap sitting in
    the `gaps` list alongside a clean recent session must not flip this to 502."""
    report = BackfillReport(instruments=[
        InstrumentReport(symbol="AAPL", exchange="XNAS", fetched=75, inserted=75,
                         recent_session_gap_pct=Decimal("0")),
    ])
    report.instruments[0].gaps = [GapDay(day=date(2026, 9, 25), expected=78, actual=20)]
    app.dependency_overrides[get_backfill_runner] = lambda: (lambda: report)

    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "supersecret"})

    assert resp.status_code == 200


def test_runner_returning_none_returns_503(job_secret):
    app.dependency_overrides[get_backfill_runner] = lambda: (lambda: None)

    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "supersecret"})

    assert resp.status_code == 503
