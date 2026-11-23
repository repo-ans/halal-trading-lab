"""Replay Trading: a human clicking Buy/Sell against a replaying chart of
already-recorded historical bars, with a live dummy balance (CLAUDE.md
section 1 / the "Simul8or"-style screen). Any authenticated user may start a
session (not admin-gated) -- the compliance gate, not an authorization check,
decides whether a given instrument can be traded, exactly like Strategy Lab's
/strategy-runs."""
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthUser, current_user, is_owner
from app.db import get_session
from app.models.replay import ReplayFill, ReplaySession
from app.rate_limit import REPLAY_ORDER_LIMITER, REPLAY_SESSION_LIMITER
from app.services.compliance import ComplianceBlocked
from app.services.replay_engine import (
    InstrumentNotFound,
    LicenceRestricted,
    NotATradingSession,
    OrderRejected,
    ReplaySessionNotActive,
    ReplaySessionNotFound,
    UnsupportedExchange,
    create_replay_session,
    end_replay_session,
    get_position,
    place_order,
)

router = APIRouter(prefix="/replay-sessions")

# Plain-language text for every OrderRejected reason this engine can raise.
# Shares wording with app/services/rejection_messages.py's Stage E messages
# wherever the same situation applies, but is its own dict rather than an
# import from there: that module is keyed off app.models.paper.REJECTION_REASONS,
# which doesn't (and shouldn't) know about replay-only reasons like
# no_next_bar_in_session.
_REJECTION_MESSAGES: dict[str, str] = {
    "position_already_open": "A position is already open -- only one at a time.",
    "no_open_position_to_sell": "There is no open position to sell.",
    "price_exceeds_max_position": "Price exceeded the max position size -- no whole shares could be bought.",
    "insufficient_cash": "Not enough cash is available to afford this trade.",
    "execution_bar_missing": "No price data is available for the next bar -- the trade could not be priced.",
    "execution_bar_missing_eod": "No price data is available to close this position at session end.",
    "no_next_bar_in_session": "This is the session's last bar -- there is no next bar left to fill at.",
    "bar_not_in_session": "That bar does not belong to this session's trading day.",
}


def _rejection_message(reason: str) -> str:
    return _REJECTION_MESSAGES.get(reason, reason)


class ReplaySessionIn(BaseModel):
    instrument_id: int
    session_day: date
    starting_cash: Decimal = Decimal("1000")
    max_position_value: Decimal = Decimal("200")
    commission_per_order: Decimal = Decimal("1.00")
    spread_bps: Decimal = Decimal("2")
    slippage_bps: Decimal = Decimal("1")


class ReplaySessionOut(BaseModel):
    id: int
    instrument_id: int
    session_day: str
    starting_cash: str
    max_position_value: str
    commission_per_order: str
    spread_bps: str
    slippage_bps: str
    status: str
    started_at: str
    finished_at: str | None


class ReplayStateOut(BaseModel):
    session: ReplaySessionOut
    position_qty: str
    position_entry_price: str | None
    cash_balance: str


class ReplayOrderIn(BaseModel):
    side: str  # 'buy' | 'sell'
    clicked_bar_ts: datetime


class ReplayFillOut(BaseModel):
    id: int
    origin: str
    side: str
    fill_ts: str
    fill_price: str
    qty: str
    commission: str
    spread_cost: str
    slippage_cost: str
    cash_balance_after: str
    realized_pnl: str | None


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def _session_out(rs: ReplaySession) -> ReplaySessionOut:
    return ReplaySessionOut(
        id=rs.id, instrument_id=rs.instrument_id, session_day=rs.session_day.isoformat(),
        starting_cash=str(rs.starting_cash), max_position_value=str(rs.max_position_value),
        commission_per_order=str(rs.commission_per_order), spread_bps=str(rs.spread_bps),
        slippage_bps=str(rs.slippage_bps), status=rs.status, started_at=_iso(rs.started_at),
        finished_at=_iso(rs.finished_at),
    )


def _fill_out(f: ReplayFill) -> ReplayFillOut:
    return ReplayFillOut(
        id=f.id, origin=f.origin, side=f.side, fill_ts=_iso(f.fill_ts), fill_price=str(f.fill_price),
        qty=str(f.qty), commission=str(f.commission), spread_cost=str(f.spread_cost),
        slippage_cost=str(f.slippage_cost), cash_balance_after=str(f.cash_balance_after),
        realized_pnl=None if f.realized_pnl is None else str(f.realized_pnl),
    )


def _get_owned_session(session: Session, replay_session_id: int, user: AuthUser) -> ReplaySession:
    """Ownership is replay_sessions.user_id -- never leak whether a session
    exists to someone who doesn't own it: not-found and not-yours both return
    404, same convention as /strategy-runs."""
    rs = session.get(ReplaySession, replay_session_id)
    if rs is None or rs.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "replay session not found")
    return rs


def _state_out(session: Session, rs: ReplaySession) -> ReplayStateOut:
    position = get_position(session, rs)
    return ReplayStateOut(
        session=_session_out(rs), position_qty=str(position.qty),
        position_entry_price=None if position.entry_price is None else str(position.entry_price),
        cash_balance=str(position.cash_balance),
    )


@router.post("", response_model=ReplayStateOut, status_code=status.HTTP_201_CREATED)
def create_session(
    body: ReplaySessionIn,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ReplayStateOut:
    now = datetime.now(timezone.utc)
    REPLAY_SESSION_LIMITER.check(str(user.id), now)
    try:
        rs = create_replay_session(
            session, user_id=user.id, instrument_id=body.instrument_id,
            session_day=body.session_day, starting_cash=body.starting_cash,
            max_position_value=body.max_position_value,
            commission_per_order=body.commission_per_order, spread_bps=body.spread_bps,
            slippage_bps=body.slippage_bps, now=now, caller_is_owner=is_owner(user),
        )
    except InstrumentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"instrument {exc.instrument_id} not found")
    except UnsupportedExchange as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"instrument {exc.instrument_id}'s exchange {exc.exchange!r} has "
                            "no market calendar configured")
    except LicenceRestricted:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This instrument's price data currently comes from a development-only "
            "source (yfinance) that is not licensed for display to anyone but the "
            "app owner.")
    except NotATradingSession as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{exc.session_day.isoformat()} is not a trading session on "
                            f"the {exc.calendar} calendar")
    except ComplianceBlocked as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"instrument is not tradable: {exc.reason}")
    return _state_out(session, rs)


@router.get("/{replay_session_id}", response_model=ReplayStateOut)
def get_session_state(
    replay_session_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ReplayStateOut:
    rs = _get_owned_session(session, replay_session_id, user)
    return _state_out(session, rs)


@router.get("/{replay_session_id}/fills", response_model=list[ReplayFillOut])
def get_fills(
    replay_session_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[ReplayFillOut]:
    _get_owned_session(session, replay_session_id, user)
    rows = session.scalars(
        select(ReplayFill).where(ReplayFill.replay_session_id == replay_session_id)
        .order_by(ReplayFill.id)
    ).all()
    return [_fill_out(f) for f in rows]


@router.post("/{replay_session_id}/orders", response_model=ReplayStateOut, status_code=status.HTTP_201_CREATED)
def post_order(
    replay_session_id: int,
    body: ReplayOrderIn,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ReplayStateOut:
    now = datetime.now(timezone.utc)
    REPLAY_ORDER_LIMITER.check(str(user.id), now)
    if body.side not in ("buy", "sell"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "side must be 'buy' or 'sell'")
    clicked_bar_ts = body.clicked_bar_ts
    if clicked_bar_ts.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "clicked_bar_ts must be timezone-aware")
    clicked_bar_ts = clicked_bar_ts.astimezone(timezone.utc)

    rs = _get_owned_session(session, replay_session_id, user)
    try:
        place_order(session, replay_session_id, user.id, side=body.side, clicked_bar_ts=clicked_bar_ts)
    except ReplaySessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "replay session not found")
    except ReplaySessionNotActive:
        raise HTTPException(status.HTTP_409_CONFLICT, "this replay session has already ended")
    except OrderRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "error": "order_rejected", "reason": exc.reason, "message": _rejection_message(exc.reason),
        })
    session.refresh(rs)
    return _state_out(session, rs)


@router.post("/{replay_session_id}/end", response_model=ReplayStateOut)
def end_session(
    replay_session_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ReplayStateOut:
    now = datetime.now(timezone.utc)
    _get_owned_session(session, replay_session_id, user)
    try:
        rs = end_replay_session(session, replay_session_id, user.id, now)
    except ReplaySessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "replay session not found")
    except ReplaySessionNotActive:
        raise HTTPException(status.HTTP_409_CONFLICT, "this replay session has already ended")
    except OrderRejected as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "error": "eod_exit_failed", "reason": exc.reason, "message": _rejection_message(exc.reason),
        })
    return _state_out(session, rs)
