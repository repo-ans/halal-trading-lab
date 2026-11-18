"""app/demo_mode.py's guard, main.py's demo-mode wiring (no scheduler, no startup
backfill, /jobs/backfill refuses), and scripts/seed_demo.py's own safety checks.
Mixed: most of these are pure; a few are DB-writing against db_test (run_db_tests)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID

import psycopg
import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
import scripts.seed_demo as seed_demo
from app.config import Settings, get_settings
from app.db import read_only_session
from app.demo_mode import DemoModeUnsafe, assert_demo_db_is_safe
from app.models.instrument import Instrument
from tests.conftest import run_db_tests

client = TestClient(main_module.app)


def _settings(**overrides) -> Settings:
    base = {"database_url": "postgresql+psycopg://u:p@localhost:5434/halal_demo"}
    base.update(overrides)
    return Settings(**base)


# --- guard: demo mode ---

def test_demo_mode_passes_when_host_port_and_db_all_match():
    assert_demo_db_is_safe(_settings(app_mode="demo"))  # no raise


def test_demo_mode_refuses_non_local_host():
    with pytest.raises(DemoModeUnsafe):
        assert_demo_db_is_safe(_settings(
            app_mode="demo",
            database_url="postgresql+psycopg://u:p@example.com:5434/halal_demo"))


def test_demo_mode_refuses_wrong_port():
    with pytest.raises(DemoModeUnsafe):
        assert_demo_db_is_safe(_settings(
            app_mode="demo",
            database_url="postgresql+psycopg://u:p@localhost:5432/halal_demo"))


def test_demo_mode_refuses_wrong_database_name():
    with pytest.raises(DemoModeUnsafe):
        assert_demo_db_is_safe(_settings(
            app_mode="demo",
            database_url="postgresql+psycopg://u:p@localhost:5434/halal_test"))


def test_demo_mode_accepts_127_0_0_1_too():
    assert_demo_db_is_safe(_settings(
        app_mode="demo",
        database_url="postgresql+psycopg://u:p@127.0.0.1:5434/halal_demo"))


# --- guard: production (or any non-demo) mode ---

def test_production_mode_refuses_the_demo_database_name():
    with pytest.raises(DemoModeUnsafe):
        assert_demo_db_is_safe(_settings(
            app_mode="production",
            database_url="postgresql+psycopg://u:p@db.supabase.co:5432/halal_demo"))


def test_production_mode_allows_any_other_database_regardless_of_host():
    assert_demo_db_is_safe(_settings(
        app_mode="production",
        database_url="postgresql+psycopg://u:p@db.supabase.co:5432/postgres"))  # no raise


def test_invalid_app_mode_rejected_at_settings_construction():
    with pytest.raises(Exception):  # pydantic ValidationError
        _settings(app_mode="staging")


# --- main.py: no scheduler / no startup backfill in demo mode ---

async def test_lifespan_skips_scheduler_and_backfill_in_demo_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_mode", "demo")
    fake_scheduler = MagicMock()
    monkeypatch.setattr(main_module, "create_scheduler", lambda: fake_scheduler)
    fake_runner = MagicMock()
    monkeypatch.setattr(main_module, "run_backfill_job", fake_runner)

    async with main_module.lifespan(main_module.app):
        pass

    fake_scheduler.start.assert_not_called()
    fake_scheduler.shutdown.assert_not_called()
    fake_runner.assert_not_called()


async def test_lifespan_starts_scheduler_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_mode", "production")
    fake_scheduler = MagicMock()
    monkeypatch.setattr(main_module, "create_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr(main_module, "run_backfill_job", MagicMock())

    async with main_module.lifespan(main_module.app):
        pass

    fake_scheduler.start.assert_called_once()
    fake_scheduler.shutdown.assert_called_once()


def test_backfill_route_refuses_in_demo_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_mode", "demo")
    monkeypatch.setattr(get_settings(), "job_secret", "s3cret")

    resp = client.post("/jobs/backfill", headers={"X-Job-Secret": "s3cret"})

    assert resp.status_code == 503
    assert "demo mode" in resp.json()["detail"]


def test_config_route_reports_app_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_mode", "demo")
    resp = client.get("/config")
    assert resp.status_code == 200
    assert resp.json() == {"app_mode": "demo"}


# --- seed_demo.py: guards ---

def test_seed_demo_main_calls_the_shared_guard_first(monkeypatch):
    sentinel = DemoModeUnsafe("sentinel")

    def _raise(settings):
        raise sentinel

    monkeypatch.setattr(seed_demo, "assert_demo_db_is_safe", _raise)
    monkeypatch.setattr(seed_demo, "_resolve_source_url", lambda: pytest.fail("should not be reached"))

    with pytest.raises(DemoModeUnsafe):
        seed_demo.main()


def test_refuse_if_same_database():
    with pytest.raises(SystemExit):
        seed_demo._refuse_if_same_database(
            "postgresql+psycopg://u:p@localhost:5434/halal_demo",
            "postgresql+psycopg://u:p@localhost:5434/halal_demo",
        )


def test_refuse_if_same_database_allows_distinct_targets():
    seed_demo._refuse_if_same_database(  # no raise
        "postgresql+psycopg://u:p@db.supabase.co:5432/postgres",
        "postgresql+psycopg://u:p@localhost:5434/halal_demo",
    )


@run_db_tests
def test_refuse_if_target_has_non_demo_data(db_conn):
    inst = Instrument(symbol="ZNONDEMO", exchange="XNAS", currency="USD", name="x")
    db_conn.add(inst)
    db_conn.flush()
    from app.services.compliance import create_compliance_check
    create_compliance_check(
        db_conn, instrument_id=inst.id, status="halal", source="manual",
        checked_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 2, 1, tzinfo=timezone.utc), created_by=None,
        evidence_url="https://example.com", notes="a real screening note, not a fixture",
    )
    db_conn.flush()

    with pytest.raises(SystemExit):
        seed_demo._refuse_if_target_has_non_demo_data(db_conn)


@run_db_tests
def test_refuse_if_target_has_non_demo_data_allows_only_fixtures(db_conn):
    inst = Instrument(symbol="ZONLYDEMO", exchange="XNAS", currency="USD", name="x")
    db_conn.add(inst)
    db_conn.flush()
    from app.services.compliance import create_compliance_check
    create_compliance_check(
        db_conn, instrument_id=inst.id, status="halal", source="manual",
        checked_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 2, 1, tzinfo=timezone.utc), created_by=seed_demo.DEMO_CREATED_BY,
        evidence_url=seed_demo.DEMO_EVIDENCE_URL, notes=seed_demo.DEMO_NOTES,
    )
    db_conn.flush()

    seed_demo._refuse_if_target_has_non_demo_data(db_conn)  # no raise


# --- seed_demo.py: "stop and tell me" instead of bypassing validation ---

def test_create_fixture_stops_if_the_real_validation_rejects_it(monkeypatch):
    calls = []
    monkeypatch.setattr(seed_demo, "validate_new_check", lambda **kw: "simulated rejection")
    monkeypatch.setattr(seed_demo, "create_compliance_check",
                        lambda *a, **kw: calls.append((a, kw)))

    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    spec = {"status": "halal", "checked_at": now - timedelta(days=1),
           "expires_at": now + timedelta(days=90)}

    with pytest.raises(SystemExit):
        seed_demo._create_fixture(MagicMock(), "DEMO-OK", 1, spec, now)
    assert calls == []


def test_all_six_fixture_specs_pass_the_real_validation():
    """Every spec build_fixture_specs produces must actually be acceptable to the
    SAME validate_new_check the admin route uses -- if one didn't, seed_demo.py
    would stop rather than insert it directly (the test above proves that half;
    this proves the specs as shipped never hit that path)."""
    from app.services.compliance import validate_new_check

    now = datetime.now(timezone.utc)
    for symbol, spec in seed_demo.build_fixture_specs(now).items():
        error = validate_new_check(
            status=spec["status"], checked_at=spec["checked_at"], expires_at=spec["expires_at"],
            evidence_url=seed_demo.DEMO_EVIDENCE_URL, evidence_file_path=None, now=now,
        )
        assert error is None, f"{symbol}: {error}"


def test_fixture_notes_and_evidence_url_match_the_required_convention():
    assert seed_demo.DEMO_NOTES.startswith("DEMO FIXTURE, NOT A RULING")
    assert seed_demo.DEMO_EVIDENCE_URL == "https://example.invalid/demo-fixture"
    assert seed_demo.DEMO_CREATED_BY == UUID("00000000-0000-0000-0000-000000000000")


# --- the real read-only-transaction proof (not a MagicMock) ---

@run_db_tests
def test_read_only_session_physically_rejects_a_write(live_db_url):
    with pytest.raises(sa.exc.DBAPIError) as exc_info:
        with read_only_session(live_db_url) as session:
            session.add(Instrument(symbol="ZROWRITE", exchange="XNAS", currency="USD", name="x"))
            session.flush()

    assert isinstance(exc_info.value.orig, psycopg.errors.ReadOnlySqlTransaction)
