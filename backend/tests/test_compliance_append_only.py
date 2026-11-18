"""DB-writing: the append-only trigger (migration 0003), the flip-then-insert
service function, the partial unique index backstop, and assert_tradable's DB
integration. Runs against the local throwaway test database (db_conn,
RUN_DB_TESTS=1 -- see conftest.py and CLAUDE.md section 2)."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models.compliance import ComplianceCheck
from app.models.instrument import Instrument
from app.services.compliance import (
    ComplianceBlocked,
    ComplianceConflict,
    assert_tradable,
    create_compliance_check,
)
from tests.conftest import run_db_tests

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _make_instrument(db_conn, symbol="ZCOMP") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
    db_conn.add(inst)
    db_conn.flush()
    return inst


def _make_check(db_conn, instrument_id, status="halal", checked_at=None, expires_at=None):
    check = create_compliance_check(
        db_conn, instrument_id=instrument_id, status=status, source="manual",
        checked_at=checked_at or NOW - timedelta(days=5),
        expires_at=expires_at or NOW + timedelta(days=85),
        created_by=None, evidence_url="https://example.com/doc",
    )
    db_conn.commit()
    return check


# --- append-only trigger ---

@run_db_tests
def test_delete_is_rejected_by_trigger(db_conn):
    inst = _make_instrument(db_conn)
    check = _make_check(db_conn, inst.id)

    with pytest.raises(sa.exc.DBAPIError, match="append-only"):
        db_conn.execute(sa.text("DELETE FROM compliance_checks WHERE id=:id"), {"id": check.id})
        db_conn.commit()
    db_conn.rollback()


@run_db_tests
def test_update_of_non_is_current_column_is_rejected_by_trigger(db_conn):
    inst = _make_instrument(db_conn)
    check = _make_check(db_conn, inst.id)

    with pytest.raises(sa.exc.DBAPIError, match="append-only"):
        db_conn.execute(
            sa.text("UPDATE compliance_checks SET status='not_halal' WHERE id=:id"),
            {"id": check.id})
        db_conn.commit()
    db_conn.rollback()


@run_db_tests
def test_is_current_only_update_is_allowed(db_conn):
    inst = _make_instrument(db_conn)
    check = _make_check(db_conn, inst.id)

    db_conn.execute(
        sa.text("UPDATE compliance_checks SET is_current=false WHERE id=:id"),
        {"id": check.id})
    db_conn.commit()  # must not raise

    db_conn.expire_all()
    refreshed = db_conn.get(ComplianceCheck, check.id)
    assert refreshed.is_current is False


# --- the flip ---

@run_db_tests
def test_flip_replaces_current_and_keeps_history(db_conn):
    inst = _make_instrument(db_conn)
    first = _make_check(db_conn, inst.id, status="halal")
    second = create_compliance_check(
        db_conn, instrument_id=inst.id, status="not_halal", source="manual",
        checked_at=NOW, expires_at=NOW + timedelta(days=90), created_by=None,
    )
    db_conn.commit()

    db_conn.expire_all()
    assert db_conn.get(ComplianceCheck, first.id).is_current is False
    assert db_conn.get(ComplianceCheck, second.id).is_current is True

    count = db_conn.scalar(
        sa.select(sa.func.count()).select_from(ComplianceCheck)
        .where(ComplianceCheck.instrument_id == inst.id)
    )
    assert count == 2  # append-only: both rows still exist


# --- unique-index backstop + conflict translation ---

@run_db_tests
def test_unique_index_rejects_two_current_rows_for_same_instrument(db_conn):
    """Proves uq_compliance_one_current (migration 0001) is really enforced -- the
    backstop create_compliance_check's own conflict handling relies on."""
    inst = _make_instrument(db_conn)
    _make_check(db_conn, inst.id)

    with pytest.raises(sa.exc.IntegrityError):
        db_conn.execute(sa.text("""
            INSERT INTO compliance_checks
                (instrument_id, status, source, checked_at, expires_at, is_current)
            VALUES (:iid, 'not_halal', 'manual', :checked, :expires, true)
        """), {"iid": inst.id, "checked": NOW, "expires": NOW + timedelta(days=90)})
        db_conn.commit()
    db_conn.rollback()


@run_db_tests
def test_flush_integrity_error_becomes_compliance_conflict_not_500(db_conn, monkeypatch):
    """A genuine two-connection race is awkward to construct deterministically in a
    single-connection test; this proves the half that actually matters for "409, not
    500": create_compliance_check catches ANY IntegrityError at flush (which is
    exactly what a real concurrent unique-index violation looks like) and re-raises
    ComplianceConflict, never letting it propagate as a raw exception."""
    inst = _make_instrument(db_conn)

    def failing_flush(*args, **kwargs):
        raise sa.exc.IntegrityError("INSERT", {}, Exception("unique violation (simulated)"))

    monkeypatch.setattr(db_conn, "flush", failing_flush)

    with pytest.raises(ComplianceConflict):
        create_compliance_check(
            db_conn, instrument_id=inst.id, status="halal", source="manual",
            checked_at=NOW, expires_at=NOW + timedelta(days=90), created_by=None,
            evidence_url="https://example.com/doc",
        )


# --- assert_tradable DB integration ---

@run_db_tests
def test_assert_tradable_passes_and_returns_check_id(db_conn):
    inst = _make_instrument(db_conn)
    check = _make_check(db_conn, inst.id, status="halal")

    assert assert_tradable(db_conn, inst.id, NOW) == check.id


@run_db_tests
def test_assert_tradable_raises_for_no_record(db_conn):
    inst = _make_instrument(db_conn)
    db_conn.commit()

    with pytest.raises(ComplianceBlocked) as exc_info:
        assert_tradable(db_conn, inst.id, NOW)
    assert exc_info.value.reason == "no_record"


@run_db_tests
def test_assert_tradable_raises_for_expired(db_conn):
    inst = _make_instrument(db_conn)
    _make_check(db_conn, inst.id, status="halal",
               checked_at=NOW - timedelta(days=100), expires_at=NOW - timedelta(days=1))

    with pytest.raises(ComplianceBlocked) as exc_info:
        assert_tradable(db_conn, inst.id, NOW)
    assert exc_info.value.reason == "expired"
