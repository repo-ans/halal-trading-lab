"""The ONLY path to instrument/price data. supabase-js is Auth only -- the frontend
must never read these tables directly through it (CLAUDE.md, owner instruction)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AuthUser, current_user
from app.db import get_session
from app.market_calendar import EXCHANGE_CALENDARS, exchange_timezone
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar

router = APIRouter()

MAX_BARS_PER_REQUEST = 5000
SUPPORTED_INTERVAL = "5m"


class InstrumentOut(BaseModel):
    id: int
    symbol: str
    exchange: str
    currency: str
    timezone: str  # IANA name, e.g. "America/New_York" -- see market_calendar.py
    latest_bar_ts: str | None  # ISO-8601 UTC, e.g. "2026-11-17T20:55:00Z"
    bar_count: int
    compliance: None = None  # Stage C wires this up; always null for now


class BarOut(BaseModel):
    ts: int  # epoch seconds UTC -- the format lightweight-charts' time axis expects
    open: str
    high: str
    low: str
    close: str
    volume: int
    source: str


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments(
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[InstrumentOut]:
    instruments = session.scalars(
        select(Instrument).where(Instrument.active.is_(True)).order_by(Instrument.symbol)
    ).all()

    out = []
    for inst in instruments:
        calendar = EXCHANGE_CALENDARS.get(inst.exchange)
        tz = exchange_timezone(calendar) if calendar else "UTC"
        latest_ts, count = session.execute(
            select(func.max(PriceBar.ts), func.count()).where(
                PriceBar.instrument_id == inst.id,
                PriceBar.interval == SUPPORTED_INTERVAL,
            )
        ).one()
        out.append(InstrumentOut(
            id=inst.id,
            symbol=inst.symbol,
            exchange=inst.exchange,
            currency=inst.currency,
            timezone=tz,
            latest_bar_ts=_to_iso_z(latest_ts),
            bar_count=count or 0,
        ))
    return out


@router.get("/instruments/{instrument_id}/bars", response_model=list[BarOut])
def get_bars(
    instrument_id: int,
    from_: int = Query(..., alias="from", description="epoch seconds UTC, inclusive"),
    to: int = Query(..., description="epoch seconds UTC, exclusive"),
    interval: str = Query(SUPPORTED_INTERVAL),
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[BarOut]:
    if interval != SUPPORTED_INTERVAL:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"unsupported interval {interval!r}; only "
                            f"{SUPPORTED_INTERVAL!r} exists")
    if from_ >= to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "'from' must be < 'to'")

    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "instrument not found")

    start = datetime.fromtimestamp(from_, tz=timezone.utc)
    end = datetime.fromtimestamp(to, tz=timezone.utc)

    # Count first -- never run the full SELECT, let alone return a truncated result,
    # for an oversized range. Rejected outright, not silently capped (CLAUDE.md:
    # never interpolate, fill, or otherwise invent what's shown).
    count = session.scalar(
        select(func.count()).select_from(PriceBar).where(
            PriceBar.instrument_id == instrument_id,
            PriceBar.interval == interval,
            PriceBar.ts >= start,
            PriceBar.ts < end,
        )
    )
    if count > MAX_BARS_PER_REQUEST:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"range contains {count} bars, over the {MAX_BARS_PER_REQUEST}-bar cap -- "
            "narrow the range")

    rows = session.scalars(
        select(PriceBar).where(
            PriceBar.instrument_id == instrument_id,
            PriceBar.interval == interval,
            PriceBar.ts >= start,
            PriceBar.ts < end,
        ).order_by(PriceBar.ts)
    ).all()

    return [
        BarOut(ts=int(r.ts.timestamp()), open=str(r.open), high=str(r.high),
              low=str(r.low), close=str(r.close), volume=r.volume, source=r.source)
        for r in rows
    ]


def _to_iso_z(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat().replace("+00:00", "Z")
