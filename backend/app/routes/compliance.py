"""Compliance writes are admin-only (CLAUDE.md section 5 + owner instruction).
Reads go through GET /watchlist (watchlist.py), open to any authenticated user."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import AuthUser, require_admin
from app.config import get_settings
from app.db import get_session
from app.models.instrument import Instrument
from app.providers.compliance.zoya import ZoyaApiError, ZoyaProvider
from app.services.compliance import (
    ComplianceConflict,
    create_compliance_check,
    default_expires_at,
    validate_new_check,
)
from app.services.compliance_csv import RowResult, parse_and_validate_csv

router = APIRouter(prefix="/compliance")

VALID_STATUSES = ("COMPLIANT", "NON_COMPLIANT", "QUESTIONABLE", "UNRATED")
VALID_SOURCES = ("manual", "zoya", "musaffa")


class ComplianceIn(BaseModel):
    instrument_id: int
    status: str
    source: str
    evidence_url: str | None = None
    notes: str | None = None
    checked_at: datetime
    expires_at: datetime | None = None


class ComplianceOut(BaseModel):
    id: int
    instrument_id: int
    status: str
    source: str
    evidence_url: str | None
    notes: str | None
    checked_at: datetime
    expires_at: datetime
    is_current: bool


def _require_aware(dt: datetime, field_name: str) -> datetime:
    if dt.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{field_name} must be timezone-aware")
    return dt.astimezone(timezone.utc)


@router.post("", response_model=ComplianceOut, status_code=status.HTTP_201_CREATED)
def create_compliance(
    body: ComplianceIn,
    user: AuthUser = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ComplianceOut:
    if body.status not in VALID_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"status must be one of {VALID_STATUSES}")
    if body.source not in VALID_SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"source must be one of {VALID_SOURCES}")
    if session.get(Instrument, body.instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    now = datetime.now(timezone.utc)
    checked_at = _require_aware(body.checked_at, "checked_at")
    settings = get_settings()
    expires_at = (_require_aware(body.expires_at, "expires_at") if body.expires_at is not None
                 else default_expires_at(checked_at, settings.compliance_default_validity_days))

    error = validate_new_check(
        status=body.status, checked_at=checked_at, expires_at=expires_at,
        evidence_url=body.evidence_url, evidence_file_path=None, now=now,
    )
    if error is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error)

    try:
        check = create_compliance_check(
            session, instrument_id=body.instrument_id, status=body.status,
            source=body.source, checked_at=checked_at, expires_at=expires_at,
            created_by=user.id, evidence_url=body.evidence_url, notes=body.notes,
        )
        session.commit()
    except ComplianceConflict:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a concurrent compliance update is already in progress for this instrument")

    return ComplianceOut(
        id=check.id, instrument_id=check.instrument_id, status=check.status,
        source=check.source, evidence_url=check.evidence_url, notes=check.notes,
        checked_at=check.checked_at, expires_at=check.expires_at,
        is_current=check.is_current,
    )


def _row_dict(r: RowResult) -> dict:
    return {"row_number": r.row_number, "accepted": r.accepted,
           "symbol": r.symbol, "exchange": r.exchange, "reason": r.reason}


@router.post("/csv/dry-run")
async def csv_dry_run(
    file: UploadFile = File(...),
    user: AuthUser = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    raw = await file.read()
    now = datetime.now(timezone.utc)
    result = parse_and_validate_csv(
        raw, session, now, get_settings().compliance_default_validity_days)
    if result.file_error is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result.file_error)
    return {"rows": [_row_dict(r) for r in result.rows], "all_accepted": result.all_accepted}


@router.post("/csv/confirm")
async def csv_confirm(
    file: UploadFile = File(...),
    user: AuthUser = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Re-parses and re-validates the same file independently (stateless -- no
    server-side cache of the dry run to get out of sync with) and applies ALL rows
    in one commit, or none."""
    raw = await file.read()
    now = datetime.now(timezone.utc)
    result = parse_and_validate_csv(
        raw, session, now, get_settings().compliance_default_validity_days)
    if result.file_error is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result.file_error)
    if not result.all_accepted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "one or more rows were rejected; nothing was applied")

    try:
        for r in result.rows:
            create_compliance_check(
                session, instrument_id=r.instrument_id, status=r.status, source=r.source,
                checked_at=r.checked_at, expires_at=r.expires_at, created_by=user.id,
                evidence_url=r.evidence_url, notes=r.notes,
            )
        session.commit()
    except ComplianceConflict:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a concurrent compliance update conflicted with this import; nothing was applied")

    return {"applied": len(result.rows)}


class ZoyaLookupOut(BaseModel):
    available: bool  # False means Zoya has no coverage for this symbol
    is_sandbox: bool  # True means randomized dummy data -- never a real ruling
    status: str | None = None
    purification_ratio: float | None = None
    report_date: datetime | None = None
    zoya_name: str | None = None
    zoya_exchange: str | None = None


@router.get("/zoya-lookup", response_model=ZoyaLookupOut)
def zoya_lookup(
    instrument_id: int,
    user: AuthUser = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ZoyaLookupOut:
    """A SUGGESTION only -- never writes anything. CLAUDE.md section 5: a human
    still reviews this, supplies real evidence, and submits through the
    existing POST /compliance (or CSV import) to actually create a record."""
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    settings = get_settings()
    api_key = settings.zoya_api_key.get_secret_value()
    if not api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Zoya lookup is not configured (ZOYA_API_KEY is empty)")

    provider = ZoyaProvider(
        api_key=api_key, api_url=settings.zoya_api_url,
        timeout_seconds=settings.zoya_timeout_seconds,
        default_validity_days=settings.compliance_default_validity_days,
    )
    try:
        report = provider.get_report(instrument.symbol)
    except ZoyaApiError:
        # Never surface the SDK/HTTP exception's own text (CLAUDE.md's secret
        # -handling convention, applied here too even though there's no secret
        # in this particular exception -- a consistent habit beats remembering
        # which exceptions are "safe" to echo).
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Zoya API request failed -- try again shortly")

    if report is None:
        return ZoyaLookupOut(available=False, is_sandbox=provider.is_sandbox)

    return ZoyaLookupOut(
        available=True, is_sandbox=provider.is_sandbox, status=report.status,
        purification_ratio=report.purification_ratio, report_date=report.report_date,
        zoya_name=report.name, zoya_exchange=report.exchange,
    )
