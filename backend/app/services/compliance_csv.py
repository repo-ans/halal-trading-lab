"""CSV import for compliance records. Dry-run and confirm (app/routes/compliance.py)
both call parse_and_validate_csv -- the exact same parsing and validation, so the two
paths can never silently diverge. Unknown instruments are rejected and listed, never
auto-created (CLAUDE.md section 5: the app never invents data)."""
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.services.compliance import default_expires_at, validate_new_check

MAX_ROWS = 500
MAX_FILE_BYTES = 1024 * 1024  # 1 MB
REQUIRED_COLUMNS = {"symbol", "exchange", "status", "source", "evidence_url",
                    "checked_at", "expires_at", "notes"}
VALID_STATUSES = {"COMPLIANT", "NON_COMPLIANT", "QUESTIONABLE", "UNRATED"}
VALID_SOURCES = {"manual", "zoya", "musaffa"}


@dataclass
class RowResult:
    row_number: int  # 1-based, excluding the header row
    accepted: bool
    symbol: str | None = None
    exchange: str | None = None
    reason: str | None = None  # rejection reason; None if accepted
    # Parsed fields -- populated only when accepted=True. The confirm step writes
    # these directly, so it never re-derives anything the dry run already decided.
    instrument_id: int | None = None
    status: str | None = None
    source: str | None = None
    checked_at: datetime | None = None
    expires_at: datetime | None = None
    evidence_url: str | None = None
    notes: str | None = None


@dataclass
class CsvParseResult:
    rows: list[RowResult] = field(default_factory=list)
    file_error: str | None = None  # whole-file rejection: size, header, duplicates

    @property
    def all_accepted(self) -> bool:
        return self.file_error is None and bool(self.rows) and all(r.accepted for r in self.rows)


def _parse_date(raw: str, field_name: str) -> tuple[datetime | None, str | None]:
    """YYYY-MM-DD (interpreted as 00:00 UTC) or ISO-8601 WITH an explicit offset.
    A naive ISO-8601 timestamp (no offset) is rejected -- it would be ambiguous
    about which timezone it's in, exactly the kind of silent corruption CLAUDE.md
    section 8 warns about. Returns (value, error); value is None if raw is empty."""
    raw = raw.strip()
    if not raw:
        return None, None
    if len(raw) == 10:
        try:
            d = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None, f"{field_name}: not a valid YYYY-MM-DD date ({raw!r})"
        return d.replace(tzinfo=timezone.utc), None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None, (f"{field_name}: must be YYYY-MM-DD or ISO-8601 with an "
                      f"explicit offset ({raw!r})")
    if dt.tzinfo is None:
        return None, (f"{field_name}: ISO-8601 value must include an explicit UTC "
                      f"offset ({raw!r})")
    return dt.astimezone(timezone.utc), None


def parse_and_validate_csv(
    raw_bytes: bytes,
    session: Session,
    now: datetime,
    default_validity_days: int,
) -> CsvParseResult:
    if len(raw_bytes) > MAX_FILE_BYTES:
        return CsvParseResult(file_error=f"file exceeds the {MAX_FILE_BYTES}-byte cap")

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return CsvParseResult(file_error=f"file is not valid UTF-8: {exc}")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or set(reader.fieldnames) != REQUIRED_COLUMNS:
        return CsvParseResult(file_error=f"header must be exactly: {','.join(sorted(REQUIRED_COLUMNS))}")

    raw_rows = list(reader)
    if not raw_rows:
        return CsvParseResult(file_error="no data rows")
    if len(raw_rows) > MAX_ROWS:
        return CsvParseResult(file_error=f"{len(raw_rows)} rows exceeds the {MAX_ROWS}-row cap")

    # Reject the WHOLE FILE if the same (symbol, exchange) appears twice -- silently
    # taking "the last one wins" would hide a mistake in the owner's source file.
    seen: dict[tuple[str, str], int] = {}
    for i, row in enumerate(raw_rows, start=1):
        key = (row.get("symbol", "").strip().upper(), row.get("exchange", "").strip().upper())
        if key in seen:
            return CsvParseResult(
                file_error=f"duplicate (symbol, exchange) {key} on rows {seen[key]} and {i}")
        seen[key] = i

    instruments = {
        (inst.symbol.upper(), inst.exchange.upper()): inst
        for inst in session.scalars(select(Instrument)).all()
    }

    results: list[RowResult] = []
    for i, row in enumerate(raw_rows, start=1):
        symbol = row.get("symbol", "").strip()
        exchange = row.get("exchange", "").strip()
        rr = RowResult(row_number=i, accepted=False, symbol=symbol, exchange=exchange)

        instrument = instruments.get((symbol.upper(), exchange.upper()))
        if instrument is None:
            rr.reason = f"unknown instrument {symbol}/{exchange} (never auto-created)"
            results.append(rr)
            continue

        status = row.get("status", "").strip()
        if status not in VALID_STATUSES:
            rr.reason = f"status must be one of {sorted(VALID_STATUSES)}, got {status!r}"
            results.append(rr)
            continue

        source = row.get("source", "").strip()
        if source not in VALID_SOURCES:
            rr.reason = f"source must be one of {sorted(VALID_SOURCES)}, got {source!r}"
            results.append(rr)
            continue

        checked_at, err = _parse_date(row.get("checked_at", ""), "checked_at")
        if err is not None:
            rr.reason = err
            results.append(rr)
            continue
        if checked_at is None:
            rr.reason = "checked_at is required"
            results.append(rr)
            continue

        expires_raw = row.get("expires_at", "").strip()
        if expires_raw:
            expires_at, err = _parse_date(expires_raw, "expires_at")
            if err is not None:
                rr.reason = err
                results.append(rr)
                continue
        else:
            expires_at = default_expires_at(checked_at, default_validity_days)

        evidence_url = row.get("evidence_url", "").strip() or None
        notes = row.get("notes", "").strip() or None

        error = validate_new_check(
            status=status, checked_at=checked_at, expires_at=expires_at,
            evidence_url=evidence_url, evidence_file_path=None, now=now,
        )
        if error is not None:
            rr.reason = error
            results.append(rr)
            continue

        rr.accepted = True
        rr.instrument_id = instrument.id
        rr.status = status
        rr.source = source
        rr.checked_at = checked_at
        rr.expires_at = expires_at
        rr.evidence_url = evidence_url
        rr.notes = notes
        results.append(rr)

    return CsvParseResult(rows=results)
