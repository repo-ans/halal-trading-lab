"""GET /watchlist: instrument identity + compliance status + data freshness in one
screen. Open to any authenticated user -- writes are admin-only (routes/compliance.py).
The app never computes or infers a halal status here; it only relays the current
compliance_checks record via the same gate logic the paper engine will use."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models.compliance import ComplianceCheck
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar
from app.services.compliance import OK, effective_status

router = APIRouter()


class WatchlistRow(BaseModel):
    id: int
    symbol: str
    exchange: str
    status: str | None  # the record's own status, or null if there is no record
    blocking_reason: str | None  # null if tradable; else one of the 5 gate reasons
    last_checked_at: str | None
    expires_at: str | None
    days_until_expiry: int | None
    latest_bar_ts: str | None
    latest_close: str | None  # Decimal-as-string; Stage F's Strategy Lab price warning
    evidence_url: str | None  # already validated http(s)-only at write time


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


@router.get("/watchlist", response_model=list[WatchlistRow])
def get_watchlist(
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[WatchlistRow]:
    now = datetime.now(timezone.utc)
    instruments = session.scalars(
        select(Instrument).where(Instrument.active.is_(True)).order_by(Instrument.symbol)
    ).all()

    rows = []
    for inst in instruments:
        check = session.scalar(
            select(ComplianceCheck).where(
                ComplianceCheck.instrument_id == inst.id,
                ComplianceCheck.is_current.is_(True),
            )
        )
        reason = effective_status(check, now)
        latest_bar = session.scalar(
            select(PriceBar).where(PriceBar.instrument_id == inst.id)
            .order_by(PriceBar.ts.desc()).limit(1)
        )

        rows.append(WatchlistRow(
            id=inst.id,
            symbol=inst.symbol,
            exchange=inst.exchange,
            status=check.status if check is not None else None,
            blocking_reason=None if reason == OK else reason,
            last_checked_at=_iso(check.checked_at) if check is not None else None,
            expires_at=_iso(check.expires_at) if check is not None else None,
            days_until_expiry=(check.expires_at - now).days if check is not None else None,
            latest_bar_ts=_iso(latest_bar.ts) if latest_bar is not None else None,
            latest_close=str(latest_bar.close) if latest_bar is not None else None,
            evidence_url=check.evidence_url if check is not None else None,
        ))
    return rows
