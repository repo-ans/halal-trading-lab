"""DB-writing (CSV parsing queries instruments against the DB): hardening and
dry-run/confirm semantics for CSV import. Local throwaway test database
(RUN_DB_TESTS=1)."""
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.models.compliance import ComplianceCheck
from app.models.instrument import Instrument
from app.services.compliance_csv import MAX_FILE_BYTES, MAX_ROWS, parse_and_validate_csv
from tests.conftest import run_db_tests

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
HEADER = "symbol,exchange,status,source,evidence_url,checked_at,expires_at,notes"


def _make_instrument(db_conn, symbol="ZCSV", exchange="XNAS") -> Instrument:
    inst = Instrument(symbol=symbol, exchange=exchange, currency="USD", name="x")
    db_conn.add(inst)
    db_conn.flush()
    return inst


def _csv(*rows: str) -> bytes:
    return (HEADER + "\n" + "\n".join(rows)).encode("utf-8")


@run_db_tests
def test_valid_row_is_accepted(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert result.file_error is None
    assert result.all_accepted
    assert result.rows[0].status == "COMPLIANT"
    assert result.rows[0].expires_at == datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(days=90)


@run_db_tests
def test_utf8_sig_bom_is_handled(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    # .encode("utf-8-sig") already prepends the BOM bytes -- don't also put a literal
    # ﻿ in the source string, or the decode() in the code under test only
    # strips one of the two resulting BOMs and the header match fails.
    raw = (HEADER + "\nZCSV,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,"
          ).encode("utf-8-sig")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert result.file_error is None
    assert result.all_accepted


@run_db_tests
def test_unknown_instrument_rejected_never_auto_created(db_conn):
    db_conn.commit()  # no instruments seeded
    raw = _csv("ZGHOST,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert result.file_error is None
    assert not result.rows[0].accepted
    assert "unknown instrument" in result.rows[0].reason
    ghost = db_conn.scalar(sa.select(Instrument).where(Instrument.symbol == "ZGHOST"))
    assert ghost is None


@run_db_tests
def test_duplicate_symbol_exchange_rejects_whole_file(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv(
        "ZCSV,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,",
        "ZCSV,XNAS,NON_COMPLIANT,manual,,2026-11-02,,",
    )

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert result.file_error is not None
    assert "duplicate" in result.file_error


@run_db_tests
def test_file_size_cap(db_conn):
    raw = b"x" * (MAX_FILE_BYTES + 1)
    result = parse_and_validate_csv(raw, db_conn, NOW, 90)
    assert result.file_error is not None
    assert "byte" in result.file_error


@run_db_tests
def test_row_count_cap(db_conn):
    rows = [f"Z{i},XNAS,COMPLIANT,manual,https://example.com,2026-11-01,," for i in range(MAX_ROWS + 1)]
    raw = _csv(*rows)

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert result.file_error is not None
    assert "row" in result.file_error


@run_db_tests
def test_yyyy_mm_dd_date_interpreted_as_midnight_utc(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert result.rows[0].checked_at == datetime(2026, 11, 1, tzinfo=timezone.utc)


@run_db_tests
def test_iso8601_with_explicit_offset_accepted_and_converted_to_utc(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,https://example.com,2026-11-01T09:30:00-05:00,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert result.rows[0].accepted
    assert result.rows[0].checked_at == datetime(2026, 11, 1, 14, 30, tzinfo=timezone.utc)


@run_db_tests
def test_naive_iso8601_without_offset_rejected(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,https://example.com,2026-11-01T09:30:00,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert not result.rows[0].accepted
    assert "offset" in result.rows[0].reason


@run_db_tests
def test_garbage_date_rejected(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,https://example.com,not-a-date,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert not result.rows[0].accepted


@run_db_tests
def test_wrong_header_rejected(db_conn):
    raw = b"symbol,exchange\nZCSV,XNAS"
    result = parse_and_validate_csv(raw, db_conn, NOW, 90)
    assert result.file_error is not None


@run_db_tests
def test_bad_evidence_scheme_rejected_per_row(db_conn):
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,javascript:alert(1),2026-11-01,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert not result.rows[0].accepted


@run_db_tests
def test_evidence_url_with_no_host_rejected_per_row(db_conn):
    """Stage H review finding: a scheme with no host ("https:") must be rejected
    by the CSV importer too -- validate_new_check is shared with the route, so
    this and test_create_rejects_evidence_url_with_no_host (test_compliance_route.py)
    prove the two paths can't silently diverge."""
    _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,https:,2026-11-01,,")

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert not result.rows[0].accepted


# --- dry-run never writes; confirm is all-or-nothing (full atomicity is tested at
# the route level in test_compliance_route.py, with a real HTTP confirm call) ---

@run_db_tests
def test_dry_run_parsing_never_writes(db_conn):
    inst = _make_instrument(db_conn)
    db_conn.commit()
    raw = _csv("ZCSV,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,")

    parse_and_validate_csv(raw, db_conn, NOW, 90)

    count = db_conn.scalar(
        sa.select(sa.func.count()).select_from(ComplianceCheck)
        .where(ComplianceCheck.instrument_id == inst.id)
    )
    assert count == 0


@run_db_tests
def test_mixed_batch_is_not_all_accepted(db_conn):
    _make_instrument(db_conn, symbol="ZOK")
    db_conn.commit()
    raw = _csv(
        "ZOK,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,",
        "ZGHOST,XNAS,COMPLIANT,manual,https://example.com,2026-11-01,,",  # unknown
    )

    result = parse_and_validate_csv(raw, db_conn, NOW, 90)

    assert not result.all_accepted
