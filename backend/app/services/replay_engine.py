"""Replay Trading: an interactive, single-instrument, single-session manual
buy/sell simulator against already-recorded historical bars (CLAUDE.md's
"Simul8or"-inspired screen, built 2026-11-23). Deliberately separate from
strategy_runs/paper_orders/paper_fills (Stage D/E) -- those are automated-
backtest and determinism-specific; this is a human clicking Buy/Sell while
watching a chart replay. Reuses the SAME cost model (app/services/
cost_model.py), the SAME compliance gate (evaluated once at session creation,
exactly like Stage D/E's design note -- see CLAUDE.md section 7), and the
SAME market-calendar session logic as Stage D/E, so the rules a user
experiences here are never a second, hand-copied version of Stage E's rules.

Long-only, one open position per session, max $200 position (CLAUDE.md
section 7) -- there is no "Reverse" action: CLAUDE.md section 1 rules out
shorting entirely, so closing a long position is simply Sell, never a flip
into a short. The frontend's "Flat" button is just this same Sell.

No-look-ahead (CLAUDE.md section 7, must-pass test 4): a click while looking
at bar T fills at bar T+1's open, in the SAME session, never at bar T's own
close and never across a session boundary -- applied here exactly like
Stage D's execution_ts rule, just driven by a user's click instead of a
detected MA crossover.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal, localcontext
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.market_calendar import EXCHANGE_CALENDARS, session_bar_opens, session_bounds
from app.models.instrument import Instrument
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.models.replay import ReplayFill, ReplaySession
from app.services.compliance import assert_tradable  # raises ComplianceBlocked -- propagated as-is
from app.services.cost_model import adverse_fill_price, cost_breakdown

INTERVAL_MINUTES = 5
LICENCE_RESTRICTED = "licence_restricted"


class InstrumentNotFound(Exception):
    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id


class UnsupportedExchange(Exception):
    def __init__(self, instrument_id: int, exchange: str):
        self.instrument_id = instrument_id
        self.exchange = exchange


class LicenceRestricted(Exception):
    """CLAUDE.md section 3: yfinance-sourced bars aren't licensed for display to
    anyone but the app owner. Same rule Stage D applies to a whole backtest
    (app/services/strategy_run.py::LICENCE_RESTRICTED), applied here at session
    creation instead of per-instrument-in-a-run, since a replay session is
    always exactly one instrument."""
    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id


class NotATradingSession(Exception):
    def __init__(self, session_day: date, calendar: str):
        self.session_day = session_day
        self.calendar = calendar


class ReplaySessionNotFound(Exception):
    def __init__(self, replay_session_id: int):
        self.replay_session_id = replay_session_id


class ReplaySessionNotActive(Exception):
    def __init__(self, replay_session_id: int):
        self.replay_session_id = replay_session_id


class OrderRejected(Exception):
    """A normal, expected outcome the UI should explain to the user -- never a
    bug. `reason` mirrors Stage E's paper_orders.rejection_reason vocabulary
    wherever the same situation applies (price_exceeds_max_position,
    insufficient_cash, position_already_open, no_open_position_to_sell,
    execution_bar_missing, execution_bar_missing_eod), plus one reason Stage E
    never needs: no_next_bar_in_session -- a manual click, unlike an automated
    signal, can happen at ANY bar the user is looking at, including the
    session's last one, which has no T+1 bar to fill at."""
    def __init__(self, reason: str):
        self.reason = reason


def _calendar_for(inst: Instrument) -> str:
    calendar = EXCHANGE_CALENDARS.get(inst.exchange)
    if calendar is None:
        raise UnsupportedExchange(inst.id, inst.exchange)
    return calendar


def _has_yfinance_data(session: Session, instrument_id: int) -> bool:
    return bool(session.scalar(
        select(exists().where(
            PriceBar.instrument_id == instrument_id, PriceBar.source == "yfinance",
        ))
    ))


def _raw_open(session: Session, instrument_id: int, ts: datetime) -> Decimal | None:
    return session.scalar(
        select(PriceBar.open).where(
            PriceBar.instrument_id == instrument_id, PriceBar.interval == SUPPORTED_INTERVAL,
            PriceBar.ts == ts,
        )
    )


def create_replay_session(
    session: Session, *, user_id: UUID, instrument_id: int, session_day: date,
    starting_cash: Decimal, max_position_value: Decimal, commission_per_order: Decimal,
    spread_bps: Decimal, slippage_bps: Decimal, now: datetime, caller_is_owner: bool = False,
) -> ReplaySession:
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise InstrumentNotFound(instrument_id)
    calendar = _calendar_for(instrument)

    if not caller_is_owner and _has_yfinance_data(session, instrument_id):
        raise LicenceRestricted(instrument_id)

    if session_bounds(session_day, calendar) is None:
        raise NotATradingSession(session_day, calendar)

    # Gate evaluated ONCE here, with the real clock -- never re-evaluated bar by
    # bar inside the replay (same reasoning as Stage D/E's design note: a replay
    # over a historical day needs a compliance record that's already current
    # today, not one back-dated to that day). Raises ComplianceBlocked if not
    # tradable; propagated as-is to the caller.
    compliance_check_id = assert_tradable(session, instrument_id, now)

    rs = ReplaySession(
        user_id=user_id, instrument_id=instrument_id, session_day=session_day,
        starting_cash=starting_cash, max_position_value=max_position_value,
        commission_per_order=commission_per_order, spread_bps=spread_bps,
        slippage_bps=slippage_bps, compliance_check_id=compliance_check_id,
        status="active", started_at=now,
    )
    session.add(rs)
    session.commit()
    session.refresh(rs)
    return rs


@dataclass(frozen=True)
class ReplayPosition:
    qty: Decimal
    entry_price: Decimal | None  # None means flat
    cash_balance: Decimal


def get_position(session: Session, replay_session: ReplaySession) -> ReplayPosition:
    """Derived from the latest replay_fills row, never stored redundantly --
    same principle as paper_fills. Fills strictly alternate buy/sell (a sell
    always closes the ENTIRE position, exactly like Stage E), so the most
    recent row alone tells us everything: a buy means that position is still
    open, a sell (or no fills at all) means flat."""
    last_fill = session.scalars(
        select(ReplayFill).where(ReplayFill.replay_session_id == replay_session.id)
        .order_by(ReplayFill.id.desc()).limit(1)
    ).first()
    if last_fill is None:
        return ReplayPosition(Decimal(0), None, replay_session.starting_cash)
    if last_fill.side == "buy":
        return ReplayPosition(last_fill.qty, last_fill.fill_price, last_fill.cash_balance_after)
    return ReplayPosition(Decimal(0), None, last_fill.cash_balance_after)


def _owned_session(session: Session, replay_session_id: int, user_id: UUID) -> ReplaySession:
    rs = session.execute(
        select(ReplaySession).where(ReplaySession.id == replay_session_id).with_for_update()
    ).scalar_one_or_none()
    if rs is None or rs.user_id != user_id:
        raise ReplaySessionNotFound(replay_session_id)
    return rs


def _next_execution_ts(session_day: date, calendar: str, clicked_bar_ts: datetime) -> datetime:
    opens = session_bar_opens(session_day, calendar, INTERVAL_MINUTES)
    try:
        idx = opens.index(clicked_bar_ts)
    except ValueError:
        raise OrderRejected("bar_not_in_session")
    if idx + 1 >= len(opens):
        raise OrderRejected("no_next_bar_in_session")
    return opens[idx + 1]


def place_order(
    session: Session, replay_session_id: int, user_id: UUID, *, side: str, clicked_bar_ts: datetime,
) -> ReplayFill:
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    with localcontext() as ctx:
        ctx.prec = 40  # explicit -- never inherits the ambient context

        rs = _owned_session(session, replay_session_id, user_id)
        if rs.status != "active":
            raise ReplaySessionNotActive(replay_session_id)

        instrument = session.get(Instrument, rs.instrument_id)
        calendar = _calendar_for(instrument)
        execution_ts = _next_execution_ts(rs.session_day, calendar, clicked_bar_ts)

        position = get_position(session, rs)
        raw_open = _raw_open(session, rs.instrument_id, execution_ts)
        if raw_open is None:
            raise OrderRejected("execution_bar_missing")

        if side == "buy":
            if position.qty > 0:
                raise OrderRejected("position_already_open")
            fill_price = adverse_fill_price(raw_open, rs.spread_bps, rs.slippage_bps, "buy")
            qty = (rs.max_position_value / fill_price).to_integral_value(rounding=ROUND_FLOOR)
            if qty == 0:
                raise OrderRejected("price_exceeds_max_position")
            total_cost = fill_price * qty + rs.commission_per_order
            if total_cost > position.cash_balance:
                raise OrderRejected("insufficient_cash")
            spread_cost, slippage_cost = cost_breakdown(raw_open, rs.spread_bps, rs.slippage_bps, qty)
            new_cash = position.cash_balance - total_cost
            fill = ReplayFill(
                replay_session_id=rs.id, origin="manual", side="buy", fill_ts=execution_ts,
                fill_price=fill_price, qty=qty, commission=rs.commission_per_order,
                spread_cost=spread_cost, slippage_cost=slippage_cost, cash_balance_after=new_cash,
                realized_pnl=None,
            )
        else:
            if position.qty == 0:
                raise OrderRejected("no_open_position_to_sell")
            qty = position.qty
            fill_price = adverse_fill_price(raw_open, rs.spread_bps, rs.slippage_bps, "sell")
            spread_cost, slippage_cost = cost_breakdown(raw_open, rs.spread_bps, rs.slippage_bps, qty)
            proceeds = fill_price * qty - rs.commission_per_order
            new_cash = position.cash_balance + proceeds
            realized_pnl = (fill_price - position.entry_price) * qty \
                - rs.commission_per_order - rs.commission_per_order
            fill = ReplayFill(
                replay_session_id=rs.id, origin="manual", side="sell", fill_ts=execution_ts,
                fill_price=fill_price, qty=qty, commission=rs.commission_per_order,
                spread_cost=spread_cost, slippage_cost=slippage_cost, cash_balance_after=new_cash,
                realized_pnl=realized_pnl,
            )

        session.add(fill)
        session.commit()
        session.refresh(fill)
        return fill


def end_replay_session(session: Session, replay_session_id: int, user_id: UUID, now: datetime) -> ReplaySession:
    """Auto-flats any open position at the session's last bar's open (same
    EOD-exit rule as Stage E), then marks the session completed. Raises
    OrderRejected("execution_bar_missing_eod") if that bar itself is missing --
    the session stays 'active' rather than ever completing with an unresolved
    position silently dropped (CLAUDE.md must-pass test 6: never invent a
    price)."""
    with localcontext() as ctx:
        ctx.prec = 40

        rs = _owned_session(session, replay_session_id, user_id)
        if rs.status != "active":
            raise ReplaySessionNotActive(replay_session_id)

        instrument = session.get(Instrument, rs.instrument_id)
        calendar = _calendar_for(instrument)
        last_bar_ts = session_bar_opens(rs.session_day, calendar, INTERVAL_MINUTES)[-1]

        position = get_position(session, rs)
        if position.qty > 0:
            raw_open = _raw_open(session, rs.instrument_id, last_bar_ts)
            if raw_open is None:
                raise OrderRejected("execution_bar_missing_eod")
            fill_price = adverse_fill_price(raw_open, rs.spread_bps, rs.slippage_bps, "sell")
            spread_cost, slippage_cost = cost_breakdown(
                raw_open, rs.spread_bps, rs.slippage_bps, position.qty)
            proceeds = fill_price * position.qty - rs.commission_per_order
            new_cash = position.cash_balance + proceeds
            realized_pnl = (fill_price - position.entry_price) * position.qty \
                - rs.commission_per_order - rs.commission_per_order
            fill = ReplayFill(
                replay_session_id=rs.id, origin="eod_exit", side="sell", fill_ts=last_bar_ts,
                fill_price=fill_price, qty=position.qty, commission=rs.commission_per_order,
                spread_cost=spread_cost, slippage_cost=slippage_cost, cash_balance_after=new_cash,
                realized_pnl=realized_pnl,
            )
            session.add(fill)

        rs.status = "completed"
        rs.finished_at = now
        session.commit()
        session.refresh(rs)
        return rs
