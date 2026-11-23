"""Turns Stage D's signals into paper_orders/paper_fills: sizing, fills, EOD exit,
the cash ledger. Pure core (`simulate_fills`), no DB/clock/compliance gate, exactly
like app/services/strategy.py::generate_signals -- same reason: the pure function
is what makes the determinism test (must-pass #7, point 9 of the Stage E plan)
fast and exact instead of a DB round-trip.

RULES (CLAUDE.md section 7's Stage E subsection has the full write-up):
 - Affordability: qty*fill_price + commission <= cash. spread_cost/slippage_cost
   are reporting-only breakdowns, already folded into fill_price -- they are
   NEVER added to the cash check or the cash movement a second time.
 - Sizing (BUY): compute the adverse fill_price FIRST, then
   qty = floor(max_position_value / fill_price) -- so the $200 cap can never be
   exceeded by the bps adjustment. price_exceeds_max_position is decided on
   fill_price, not the raw open.
 - Sizing (SELL): always closes the ENTIRE open position. Never re-sized.
 - EOD exit: at every session's end, any instrument still holding a position gets
   a synthetic sell at that session's last bar's open. If that bar is missing,
   the order is rejected (execution_bar_missing_eod) and the instrument is
   FROZEN for the rest of the run -- no further orders processed for it
   (instrument_frozen_unresolved_eod) -- rather than ever carrying an unresolved
   position into a later session and selling it there.
 - Ledger: fill_price is rounded to 4dp FIRST; cash movement is
   fill_price*qty +/- commission with no further rounding (exact, since qty is
   a whole number and commission is already clean).
 - Ordering (an ENGINEERING CONVENTION, not a business rule -- CLAUDE.md section
   7): the global event queue is sorted by (execution_ts, sells-and-EOD-checkpoints
   before buys, instrument_id). A same-bar sell can free cash for a same-bar buy;
   which instrument's buy "wins" when two buys compete is just whichever has the
   lower instrument_id, with no trading-priority meaning.
 - Compliance: never re-checked here. Signals only exist for instruments that
   were already compliant when the run started (Stage D) -- there is no code
   path by which a blocked instrument's signal could reach this module.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_calendar import EXCHANGE_CALENDARS
from app.models.instrument import Instrument
from app.models.paper import PaperFill, PaperOrder
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.models.strategy import Signal, StrategyRun
from app.services.cost_model import adverse_fill_price as _adverse_fill_price
from app.services.cost_model import cost_breakdown as _cost_breakdown
from app.services.money import quantize_money
from app.services.strategy import _session_schedule  # reused -- same calendar/session logic as Stage D


class StrategyRunNotFound(Exception):
    def __init__(self, run_id: int):
        self.run_id = run_id


class PaperEngineAlreadyRun(Exception):
    def __init__(self, run_id: int):
        self.run_id = run_id


class EngineCurrencyMismatch(Exception):
    """Defense-in-depth invariant: Stage D's run_strategy already refuses a whole
    mixed-currency run before any signal exists (must-pass test 8), so this
    should be unreachable in practice. Raised explicitly, not asserted -- `assert`
    is stripped entirely under `python -O`, which would silently discard this
    check rather than fail loudly."""
    def __init__(self, run_id: int, instrument_id: int, instrument_currency: str,
                run_currency: str):
        self.run_id = run_id
        self.instrument_id = instrument_id
        self.instrument_currency = instrument_currency
        self.run_currency = run_currency


@dataclass(frozen=True)
class SignalIn:
    signal_id: int
    instrument_id: int
    side: str  # "buy" | "sell"
    execution_ts: datetime


@dataclass(frozen=True)
class FillResult:
    fill_ts: datetime
    fill_price: Decimal
    qty: Decimal
    commission: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    cash_balance_after: Decimal


@dataclass(frozen=True)
class OrderResult:
    instrument_id: int
    signal_id: int | None  # None for an eod_exit
    origin: str  # "signal" | "eod_exit"
    side: str  # "buy" | "sell"
    qty: Decimal
    intended_price: Decimal | None  # the bar's raw open, if known; None if the bar was missing
    execution_ts: datetime
    status: str  # "filled" | "rejected"
    rejection_reason: str | None
    fill: FillResult | None


def _session_last_bars(calendar: str, range_start: datetime, range_end: datetime,
                       interval_minutes: int = 5) -> list[datetime]:
    sessions = _session_schedule(calendar, range_start.date(), range_end.date(), interval_minutes)
    return [s[-1] for s in sessions if range_start <= s[-1] < range_end]


def _rejected(instrument_id, signal_id, origin, side, qty, intended_price, execution_ts, reason) -> OrderResult:
    return OrderResult(
        instrument_id=instrument_id, signal_id=signal_id, origin=origin, side=side, qty=qty,
        intended_price=intended_price, execution_ts=execution_ts, status="rejected",
        rejection_reason=reason, fill=None,
    )


def _attempt_buy(*, instrument_id, signal_id, execution_ts, bars, cash, has_open_position,
                 max_position_value, commission_per_order, spread_bps, slippage_bps):
    if has_open_position:
        return _rejected(instrument_id, signal_id, "signal", "buy", Decimal(0), None,
                         execution_ts, "position_already_open"), cash

    raw_open = bars.get(execution_ts)
    if raw_open is None:
        return _rejected(instrument_id, signal_id, "signal", "buy", Decimal(0), None,
                         execution_ts, "execution_bar_missing"), cash

    fill_price = _adverse_fill_price(raw_open, spread_bps, slippage_bps, "buy")
    qty = (max_position_value / fill_price).to_integral_value(rounding=ROUND_FLOOR)

    if qty == 0:
        return _rejected(instrument_id, signal_id, "signal", "buy", Decimal(0), raw_open,
                         execution_ts, "price_exceeds_max_position"), cash

    total_cost = fill_price * qty + commission_per_order
    if total_cost > cash:
        return _rejected(instrument_id, signal_id, "signal", "buy", qty, raw_open,
                         execution_ts, "insufficient_cash"), cash

    spread_cost, slippage_cost = _cost_breakdown(raw_open, spread_bps, slippage_bps, qty)
    new_cash = cash - total_cost
    order = OrderResult(
        instrument_id=instrument_id, signal_id=signal_id, origin="signal", side="buy", qty=qty,
        intended_price=raw_open, execution_ts=execution_ts, status="filled", rejection_reason=None,
        fill=FillResult(fill_ts=execution_ts, fill_price=fill_price, qty=qty,
                        commission=commission_per_order, spread_cost=spread_cost,
                        slippage_cost=slippage_cost, cash_balance_after=new_cash),
    )
    return order, new_cash


def _attempt_sell(*, instrument_id, signal_id, origin, qty, execution_ts, bars, cash,
                  commission_per_order, spread_bps, slippage_bps):
    raw_open = bars.get(execution_ts)
    if raw_open is None:
        reason = "execution_bar_missing_eod" if origin == "eod_exit" else "execution_bar_missing"
        return _rejected(instrument_id, signal_id, origin, "sell", qty, None,
                         execution_ts, reason), cash

    fill_price = _adverse_fill_price(raw_open, spread_bps, slippage_bps, "sell")
    spread_cost, slippage_cost = _cost_breakdown(raw_open, spread_bps, slippage_bps, qty)
    proceeds = fill_price * qty - commission_per_order
    new_cash = cash + proceeds
    order = OrderResult(
        instrument_id=instrument_id, signal_id=signal_id, origin=origin, side="sell", qty=qty,
        intended_price=raw_open, execution_ts=execution_ts, status="filled", rejection_reason=None,
        fill=FillResult(fill_ts=execution_ts, fill_price=fill_price, qty=qty,
                        commission=commission_per_order, spread_cost=spread_cost,
                        slippage_cost=slippage_cost, cash_balance_after=new_cash),
    )
    return order, new_cash


def simulate_fills(
    signals: list[SignalIn],
    bars_by_instrument: dict[int, dict[datetime, Decimal]],
    *,
    calendars_by_instrument: dict[int, str],
    starting_cash: Decimal,
    max_position_value: Decimal,
    commission_per_order: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
    range_start: datetime,
    range_end: datetime,
    interval_minutes: int = 5,
) -> list[OrderResult]:
    with localcontext() as ctx:
        ctx.prec = 40  # explicit -- never inherits the ambient context

        # One global event queue: every signal, plus one EOD checkpoint per
        # (instrument, session) -- a checkpoint only ever becomes a real order if
        # a position is still open when it's reached. Sort key: execution_ts, then
        # sells/checkpoints before buys, then instrument_id, then a real signal
        # before a checkpoint at the exact same (ts, instrument) -- the only way
        # that tie happens is a signal-driven SELL at a session's last bar (the
        # one case Stage D allows a signal to land there), which should close the
        # position before the checkpoint finds nothing left to do.
        events = []
        for s in signals:
            events.append((s.execution_ts, 0 if s.side == "sell" else 1, s.instrument_id, 0, "signal", s))
        for instrument_id, calendar in calendars_by_instrument.items():
            for last_bar_ts in _session_last_bars(calendar, range_start, range_end, interval_minutes):
                events.append((last_bar_ts, 0, instrument_id, 1, "eod_checkpoint", None))
        events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

        cash = starting_cash
        open_positions: dict[int, Decimal] = {}
        frozen: set[int] = set()
        results: list[OrderResult] = []

        for execution_ts, _side_pri, instrument_id, _kind_pri, kind, signal in events:
            bars = bars_by_instrument.get(instrument_id, {})

            if kind == "eod_checkpoint":
                if instrument_id in frozen:
                    continue
                qty_open = open_positions.get(instrument_id)
                if not qty_open:
                    continue
                order, cash = _attempt_sell(
                    instrument_id=instrument_id, signal_id=None, origin="eod_exit", qty=qty_open,
                    execution_ts=execution_ts, bars=bars, cash=cash,
                    commission_per_order=commission_per_order, spread_bps=spread_bps,
                    slippage_bps=slippage_bps,
                )
                results.append(order)
                if order.status == "filled":
                    del open_positions[instrument_id]
                else:
                    frozen.add(instrument_id)  # unresolved -- never carried into a later session
                continue

            # kind == "signal"
            if instrument_id in frozen:
                results.append(_rejected(instrument_id, signal.signal_id, "signal", signal.side,
                                        Decimal(0), None, execution_ts,
                                        "instrument_frozen_unresolved_eod"))
                continue

            if signal.side == "buy":
                order, cash = _attempt_buy(
                    instrument_id=instrument_id, signal_id=signal.signal_id, execution_ts=execution_ts,
                    bars=bars, cash=cash, has_open_position=instrument_id in open_positions,
                    max_position_value=max_position_value, commission_per_order=commission_per_order,
                    spread_bps=spread_bps, slippage_bps=slippage_bps,
                )
                results.append(order)
                if order.status == "filled":
                    open_positions[instrument_id] = order.qty
            else:
                qty_open = open_positions.get(instrument_id)
                if not qty_open:
                    results.append(_rejected(instrument_id, signal.signal_id, "signal", "sell",
                                            Decimal(0), None, execution_ts,
                                            "no_open_position_to_sell"))
                    continue
                order, cash = _attempt_sell(
                    instrument_id=instrument_id, signal_id=signal.signal_id, origin="signal",
                    qty=qty_open, execution_ts=execution_ts, bars=bars, cash=cash,
                    commission_per_order=commission_per_order, spread_bps=spread_bps,
                    slippage_bps=slippage_bps,
                )
                results.append(order)
                if order.status == "filled":
                    del open_positions[instrument_id]
                # if rejected (bar missing), the position simply stays open --
                # unlike an EOD-exit failure, a regular signal sell failing does
                # NOT freeze the instrument; a later signal may still try again.

        return results


def _calendar_for(inst: Instrument) -> str:
    calendar = EXCHANGE_CALENDARS.get(inst.exchange)
    if calendar is None:
        raise ValueError(f"instrument {inst.id}'s exchange {inst.exchange!r} has no market calendar")
    return calendar


def run_paper_engine(session: Session, strategy_run_id: int, now: datetime) -> StrategyRun:
    """Locks the run row, checks paper_status, writes every order/fill, and sets
    paper_status='completed' in ONE transaction -- all or nothing. A second call,
    even against a run that produced zero orders, raises PaperEngineAlreadyRun
    rather than ever duplicating. `now` is bookkeeping only (paper_completed_at),
    never fed into the engine."""
    run = session.execute(
        select(StrategyRun).where(StrategyRun.id == strategy_run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise StrategyRunNotFound(strategy_run_id)
    if run.paper_status != "not_run":
        raise PaperEngineAlreadyRun(strategy_run_id)

    compliant_ids = [int(i) for i in run.compliance_check_ids]
    instruments_by_id = {i: session.get(Instrument, i) for i in compliant_ids}
    for i, inst in instruments_by_id.items():
        if inst.currency != run.currency:
            raise EngineCurrencyMismatch(run.id, i, inst.currency, run.currency)
    calendars_by_instrument = {i: _calendar_for(instruments_by_id[i]) for i in compliant_ids}

    signals = session.scalars(
        select(Signal).where(Signal.strategy_run_id == run.id, Signal.instrument_id.in_(compliant_ids))
        .order_by(Signal.signal_ts)
    ).all()
    signal_ins = [
        SignalIn(signal_id=s.id, instrument_id=s.instrument_id, side=s.side, execution_ts=s.execution_ts)
        for s in signals
    ]

    bars_by_instrument: dict[int, dict[datetime, Decimal]] = {}
    for instrument_id in compliant_ids:
        rows = session.scalars(
            select(PriceBar).where(
                PriceBar.instrument_id == instrument_id, PriceBar.interval == SUPPORTED_INTERVAL,
                PriceBar.ts >= run.range_start, PriceBar.ts < run.range_end,
            )
        ).all()
        bars_by_instrument[instrument_id] = {r.ts: r.open for r in rows}

    results = simulate_fills(
        signal_ins, bars_by_instrument, calendars_by_instrument=calendars_by_instrument,
        starting_cash=run.starting_cash, max_position_value=run.max_position_value,
        commission_per_order=run.commission_per_order, spread_bps=run.spread_bps,
        slippage_bps=run.slippage_bps, range_start=run.range_start, range_end=run.range_end,
    )

    for r in results:
        order = PaperOrder(
            strategy_run_id=run.id, signal_id=r.signal_id, origin=r.origin,
            instrument_id=r.instrument_id, side=r.side, qty=r.qty, intended_price=r.intended_price,
            execution_ts=r.execution_ts, status=r.status, rejection_reason=r.rejection_reason,
        )
        session.add(order)
        session.flush()
        if r.fill is not None:
            session.add(PaperFill(
                order_id=order.id, fill_ts=r.fill.fill_ts, fill_price=r.fill.fill_price, qty=r.fill.qty,
                commission=r.fill.commission, spread_cost=r.fill.spread_cost,
                slippage_cost=r.fill.slippage_cost, cash_balance_after=r.fill.cash_balance_after,
            ))

    run.paper_status = "completed"
    run.paper_completed_at = now
    session.commit()
    return run
