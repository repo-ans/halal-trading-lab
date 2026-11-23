"""Pure gate tests -- effective_status/validate_* need no DB. assert_tradable's DB
integration is in test_compliance_append_only.py (it needs a real current-record
lookup)."""
from datetime import datetime, timedelta, timezone

from app.models.compliance import ComplianceCheck
from app.services.compliance import (
    OK,
    REASON_EXPIRED,
    REASON_NO_RECORD,
    effective_status,
    validate_evidence_url,
    validate_new_check,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _check(status="COMPLIANT", checked_at=None, expires_at=None) -> ComplianceCheck:
    return ComplianceCheck(
        status=status,
        checked_at=checked_at or NOW - timedelta(days=10),
        expires_at=expires_at or NOW + timedelta(days=80),
    )


# --- effective_status: every reason code + the pass case ---

def test_no_record_blocks():
    assert effective_status(None, NOW) == REASON_NO_RECORD


def test_compliant_and_valid_passes():
    assert effective_status(_check(status="COMPLIANT"), NOW) == OK


def test_non_compliant_blocks():
    assert effective_status(_check(status="NON_COMPLIANT"), NOW) == "NON_COMPLIANT"


def test_questionable_blocks():
    assert effective_status(_check(status="QUESTIONABLE"), NOW) == "QUESTIONABLE"


def test_unrated_blocks():
    assert effective_status(_check(status="UNRATED"), NOW) == "UNRATED"


def test_expired_compliant_blocks():
    check = _check(status="COMPLIANT", expires_at=NOW - timedelta(seconds=1))
    assert effective_status(check, NOW) == REASON_EXPIRED


# --- expiry boundary, exactly ---

def test_expiry_boundary_now_equal_expires_at_is_expired():
    """now < expires_at is required to pass; now == expires_at fails it."""
    check = _check(status="COMPLIANT", expires_at=NOW)
    assert effective_status(check, NOW) == REASON_EXPIRED


def test_expiry_boundary_one_second_before_still_passes():
    check = _check(status="COMPLIANT", expires_at=NOW + timedelta(seconds=1))
    assert effective_status(check, NOW) == OK


def test_expiry_boundary_one_second_after_now_still_expired():
    check = _check(status="COMPLIANT", expires_at=NOW - timedelta(seconds=1))
    assert effective_status(check, NOW) == REASON_EXPIRED


def test_blocked_status_is_blocked_even_if_also_expired():
    """A blocked status is blocked for ITS OWN reason, not "expired" -- expiry is
    only checked once a record is otherwise halal."""
    check = _check(status="QUESTIONABLE", expires_at=NOW - timedelta(days=1))
    assert effective_status(check, NOW) == "QUESTIONABLE"


# --- evidence_url scheme validation ---

def test_http_evidence_url_ok():
    assert validate_evidence_url("http://example.com/doc") is None


def test_https_evidence_url_ok():
    assert validate_evidence_url("https://example.com/doc") is None


def test_javascript_scheme_rejected():
    assert validate_evidence_url("javascript:alert(1)") is not None


def test_data_scheme_rejected():
    assert validate_evidence_url("data:text/html,<script>alert(1)</script>") is not None


def test_ftp_scheme_rejected():
    assert validate_evidence_url("ftp://example.com/doc") is not None


def test_none_evidence_url_is_valid_on_its_own():
    assert validate_evidence_url(None) is None


def test_scheme_only_no_host_rejected():
    assert validate_evidence_url("https:") is not None


def test_empty_authority_no_host_rejected():
    assert validate_evidence_url("http:///") is not None


# --- validate_new_check ---

def test_compliant_requires_evidence():
    err = validate_new_check(
        status="COMPLIANT", checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=89),
        evidence_url=None, evidence_file_path=None, now=NOW)
    assert err is not None


def test_compliant_with_evidence_url_ok():
    err = validate_new_check(
        status="COMPLIANT", checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=89),
        evidence_url="https://example.com/doc", evidence_file_path=None, now=NOW)
    assert err is None


def test_compliant_with_evidence_file_path_ok_no_url_needed():
    err = validate_new_check(
        status="COMPLIANT", checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=89),
        evidence_url=None, evidence_file_path="uploads/evidence.pdf", now=NOW)
    assert err is None


def test_non_compliant_does_not_require_evidence():
    err = validate_new_check(
        status="NON_COMPLIANT", checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=89),
        evidence_url=None, evidence_file_path=None, now=NOW)
    assert err is None


def test_checked_at_in_future_rejected():
    err = validate_new_check(
        status="NON_COMPLIANT", checked_at=NOW + timedelta(days=1), expires_at=NOW + timedelta(days=90),
        evidence_url=None, evidence_file_path=None, now=NOW)
    assert err is not None


def test_checked_at_equal_to_now_is_allowed():
    err = validate_new_check(
        status="NON_COMPLIANT", checked_at=NOW, expires_at=NOW + timedelta(days=90),
        evidence_url=None, evidence_file_path=None, now=NOW)
    assert err is None


def test_expires_at_not_after_checked_at_rejected():
    err = validate_new_check(
        status="NON_COMPLIANT", checked_at=NOW, expires_at=NOW,
        evidence_url=None, evidence_file_path=None, now=NOW)
    assert err is not None


def test_compliant_with_bad_evidence_scheme_rejected():
    err = validate_new_check(
        status="COMPLIANT", checked_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=89),
        evidence_url="javascript:alert(1)", evidence_file_path=None, now=NOW)
    assert err is not None
