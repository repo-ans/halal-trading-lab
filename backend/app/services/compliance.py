"""The compliance gate (CLAUDE.md section 5): the app NEVER computes or infers a
halal status, it only relays a human/third-party record with evidence, and blocks
trading whenever that record is missing, non-compliant, or expired. `now` is always
an explicit parameter -- never read from the wall clock here -- so a backtest's gate
decision stays reproducible (see the Stage D/E determinism note in CLAUDE.md)."""
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.compliance import ComplianceCheck

# UNCONFIRMED ASSUMPTION (CLAUDE.md section 5): doubtful and not_covered block
# trading, exactly like not_halal. The owner has not explicitly confirmed this --
# flagged here and in CLAUDE.md.
BLOCKED_STATUSES = ("not_halal", "doubtful", "not_covered")

REASON_NO_RECORD = "no_record"
REASON_EXPIRED = "expired"
OK = "ok"


class ComplianceBlocked(Exception):
    """Raised by assert_tradable. reason is one of: no_record, not_halal, doubtful,
    not_covered, expired."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ComplianceConflict(Exception):
    """Raised when a concurrent writer already flipped is_current for this
    instrument between our UPDATE and our INSERT. The route layer maps this to a
    409, never lets it surface as a raw 500."""


def effective_status(check: ComplianceCheck | None, now: datetime) -> str:
    """"ok" if tradable, else the reason it's blocked. Only status == 'halal' AND
    now < expires_at passes -- expiry is only relevant for an otherwise-halal
    record; a blocked status is blocked regardless of whether it's also expired."""
    if check is None:
        return REASON_NO_RECORD
    if check.status in BLOCKED_STATUSES:
        return check.status
    # The only remaining possibility per the DB CHECK constraint is status=='halal'.
    if now >= check.expires_at:
        return REASON_EXPIRED
    return OK


def current_check(session: Session, instrument_id: int) -> ComplianceCheck | None:
    return session.scalar(
        select(ComplianceCheck).where(
            ComplianceCheck.instrument_id == instrument_id,
            ComplianceCheck.is_current.is_(True),
        )
    )


def assert_tradable(session: Session, instrument_id: int, now: datetime) -> int:
    """Raises ComplianceBlocked(reason) if not tradable. On success, returns the
    compliance_checks.id that was used -- Stage D/E snapshots this id on the
    strategy_run so a rerun stays deterministic even if the check expires between
    runs (the gate is evaluated once at run start with the real clock; see
    CLAUDE.md's Stage D/E determinism note)."""
    check = current_check(session, instrument_id)
    status = effective_status(check, now)
    if status != OK:
        raise ComplianceBlocked(status)
    assert check is not None  # OK implies a halal, unexpired check exists
    return check.id


def validate_evidence_url(url: str | None) -> str | None:
    """http/https only -- never javascript:, data:, etc. (an XSS vector once
    rendered as a clickable link in the frontend). Returns an error message, or
    None if valid (including url is None, which is valid on its own -- the
    'halal requires evidence' rule is checked separately)."""
    if url is None:
        return None
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return f"evidence_url must be http or https, got {scheme!r}"
    return None


def validate_new_check(
    *,
    status: str,
    checked_at: datetime,
    expires_at: datetime,
    evidence_url: str | None,
    evidence_file_path: str | None,
    now: datetime,
) -> str | None:
    """Shared by POST /compliance and the CSV importer (dry-run and confirm both
    call this) so the two paths can never silently diverge. Returns an error
    message, or None if the record is valid."""
    if checked_at > now:
        return "checked_at must not be in the future"
    if expires_at <= checked_at:
        return "expires_at must be after checked_at"
    if status == "halal" and not (evidence_url or evidence_file_path):
        return "status 'halal' requires evidence_url or an evidence file"
    url_error = validate_evidence_url(evidence_url)
    if url_error is not None:
        return url_error
    return None


def default_expires_at(checked_at: datetime, validity_days: int) -> datetime:
    return checked_at + timedelta(days=validity_days)


def create_compliance_check(
    session: Session,
    *,
    instrument_id: int,
    status: str,
    source: str,
    checked_at: datetime,
    expires_at: datetime,
    created_by: UUID | None,
    evidence_url: str | None = None,
    evidence_file_path: str | None = None,
    notes: str | None = None,
) -> ComplianceCheck:
    """Append-only: UPDATEs the OLD current row's is_current to false FIRST, then
    INSERTs the new row, in one transaction -- that order means a crash between the
    two steps leaves at most zero current rows for this instrument, never two.

    Flushes, but does NOT commit -- the caller owns the transaction boundary. A
    single POST /compliance commits immediately after one call; the CSV importer
    calls this once per accepted row and commits exactly once at the end, for true
    all-or-nothing atomicity across the whole file.

    The DB trigger (migration 0003) independently enforces that nothing else can
    ever mutate or delete an existing row, and the partial unique index
    (uq_compliance_one_current, migration 0001) is the backstop against a
    concurrent writer doing the same flip at the same time -- caught here at flush
    (unique violations are not deferred in Postgres, so this never waits for
    commit) and raised as ComplianceConflict, never left to surface as a raw 500.
    The whole UPDATE+INSERT is wrapped in one try/except, not just the final
    flush() -- session.execute() auto-flushes any pending state first, so a
    conflict can in principle surface there too, not only at the explicit flush."""
    try:
        session.execute(
            update(ComplianceCheck)
            .where(ComplianceCheck.instrument_id == instrument_id,
                  ComplianceCheck.is_current.is_(True))
            .values(is_current=False)
        )
        new_check = ComplianceCheck(
            instrument_id=instrument_id, status=status, source=source,
            checked_at=checked_at, expires_at=expires_at, is_current=True,
            created_by=created_by, evidence_url=evidence_url,
            evidence_file_path=evidence_file_path, notes=notes,
        )
        session.add(new_check)
        session.flush()
    except IntegrityError as exc:
        raise ComplianceConflict() from exc
    return new_check
