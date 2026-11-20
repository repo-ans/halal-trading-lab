"""Orchestrates one strategy_runs row: validates the request, gates every
instrument once with the real clock (CLAUDE.md section 5 / section 7's Stage D/E
note), loads each compliant instrument's bars (with lookback for warm-up), calls
the pure engine (app/services/strategy.py), and persists the run + its signals.

`now` is always an explicit parameter here, never read from the wall clock inside
this module -- only the route reads `datetime.now(timezone.utc)` and passes it in.
This keeps the service testable with a fixed clock and keeps replay's "do not
re-gate" behaviour obvious: replay_strategy_run below simply never calls the gate.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.market_calendar import EXCHANGE_CALENDARS
from app.models.instrument import Instrument
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.models.strategy import Signal, StrategyRun
from app.services.compliance import ComplianceBlocked, assert_tradable
from app.services.config_hash import build_config_hash
from app.services.strategy import Bar, generate_signals

INTERVAL_MINUTES = 5
# Generous backward search cap for warm-up lookback (point 4, Stage D plan) -- a
# trading day can be as short as a half day, so this is a safety bound, not a tuned
# constant; it only has to comfortably exceed slow_period / (bars in a half day).
MAX_LOOKBACK_SEARCH_DAYS = 180

# Stage H review finding: the licence guard originally only covered
# GET /instruments/{id}/bars -- a non-owner could still run a full backtest
# against a yfinance-sourced instrument and reconstruct most of the underlying
# price series from the resulting signals/orders/fills/metrics/CSV export, none
# of which had any owner check at all. Blocked here instead, at the same gate
# that already blocks non-compliant instruments, so it's a "this instrument never
# entered the run" fact baked into blocked_instruments -- not something a route
# downstream has to remember to re-check.
LICENCE_RESTRICTED = "licence_restricted"


class UnknownInstrument(Exception):
    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id


class CurrencyMismatch(Exception):
    def __init__(self, instrument_id: int, instrument_currency: str, run_currency: str):
        self.instrument_id = instrument_id
        self.instrument_currency = instrument_currency
        self.run_currency = run_currency


class UnsupportedExchange(Exception):
    def __init__(self, instrument_id: int, exchange: str):
        self.instrument_id = instrument_id
        self.exchange = exchange


class StrategyRunNotFound(Exception):
    def __init__(self, run_id: int):
        self.run_id = run_id


def _has_yfinance_data(session: Session, instrument_id: int) -> bool:
    return bool(session.scalar(
        select(exists().where(
            PriceBar.instrument_id == instrument_id, PriceBar.source == "yfinance",
        ))
    ))


def _calendar_for(inst: Instrument) -> str:
    calendar = EXCHANGE_CALENDARS.get(inst.exchange)
    if calendar is None:
        raise UnsupportedExchange(inst.id, inst.exchange)
    return calendar


def _lookback_start_day(calendar: str, range_start: datetime, slow_period: int) -> date:
    """Earliest session day whose bars, combined with every session after it up to
    range_start, give at least slow_period calendar-expected bars before
    range_start (point 4, Stage D plan: load lookback as whole expected bars, not a
    fixed number of calendar days)."""
    from app.market_calendar import session_bar_opens  # local import: avoid a cycle at module load

    day = range_start.date()
    count = 0
    earliest = day
    for _ in range(MAX_LOOKBACK_SEARCH_DAYS):
        day -= timedelta(days=1)
        opens = session_bar_opens(day, calendar, INTERVAL_MINUTES)
        if opens:
            count += len(opens)
            earliest = day
        if count >= slow_period:
            break
    return earliest


def _load_bars(session: Session, instrument_id: int, calendar: str,
               range_start: datetime, range_end: datetime, slow_period: int) -> list[Bar]:
    lookback_day = _lookback_start_day(calendar, range_start, slow_period)
    lookback_start = datetime.combine(lookback_day, time.min, tzinfo=timezone.utc)
    rows = session.scalars(
        select(PriceBar).where(
            PriceBar.instrument_id == instrument_id,
            PriceBar.interval == SUPPORTED_INTERVAL,
            PriceBar.ts >= lookback_start,
            PriceBar.ts < range_end,
        ).order_by(PriceBar.ts)
    ).all()
    return [Bar(ts=r.ts, close=r.close) for r in rows]


def _generate_and_persist(
    session: Session, run: StrategyRun, instrument_ids: list[int],
    instruments_by_id: dict[int, Instrument],
) -> None:
    for instrument_id in instrument_ids:
        calendar = _calendar_for(instruments_by_id[instrument_id])
        bars = _load_bars(session, instrument_id, calendar, run.range_start, run.range_end,
                          run.slow_period)
        signals = generate_signals(
            bars, fast_period=run.fast_period, slow_period=run.slow_period, calendar=calendar,
            range_start=run.range_start, range_end=run.range_end,
            interval_minutes=INTERVAL_MINUTES, reset_ma_each_session=run.reset_ma_each_session,
        )
        session.add_all([
            Signal(
                strategy_run_id=run.id, instrument_id=instrument_id, signal_ts=s.signal_ts,
                side=s.side, reason=s.reason, ma_fast=s.ma_fast, ma_slow=s.ma_slow,
                execution_ts=s.execution_ts,
            )
            for s in signals
        ])


def run_strategy(
    session: Session,
    *,
    user_id: UUID,
    instrument_ids: list[int],
    range_start: datetime,
    range_end: datetime,
    fast_period: int,
    slow_period: int,
    currency: str,
    starting_cash: Decimal,
    max_position_value: Decimal,
    commission_per_order: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
    reset_ma_each_session: bool,
    now: datetime,
    caller_is_owner: bool = False,
) -> StrategyRun:
    sorted_ids = sorted(instrument_ids)
    instruments_by_id: dict[int, Instrument] = {}
    for instrument_id in sorted_ids:
        inst = session.get(Instrument, instrument_id)
        if inst is None:
            raise UnknownInstrument(instrument_id)
        if inst.currency != currency:
            raise CurrencyMismatch(instrument_id, inst.currency, currency)
        instruments_by_id[instrument_id] = inst

    config_hash = build_config_hash(
        instrument_ids=sorted_ids, range_start=range_start, range_end=range_end,
        fast_period=fast_period, slow_period=slow_period,
        reset_ma_each_session=reset_ma_each_session, currency=currency,
        starting_cash=starting_cash, max_position_value=max_position_value,
        commission_per_order=commission_per_order, spread_bps=spread_bps,
        slippage_bps=slippage_bps, interval=SUPPORTED_INTERVAL,
    )

    run = StrategyRun(
        user_id=user_id, currency=currency, instrument_ids=sorted_ids,
        range_start=range_start, range_end=range_end, fast_period=fast_period,
        slow_period=slow_period, reset_ma_each_session=reset_ma_each_session,
        starting_cash=starting_cash, max_position_value=max_position_value,
        commission_per_order=commission_per_order, spread_bps=spread_bps,
        slippage_bps=slippage_bps, config_hash=config_hash, status="running",
        started_at=now, blocked_instruments={}, compliance_check_ids={},
    )
    session.add(run)
    session.flush()  # assigns run.id

    blocked: dict[str, str] = {}
    check_ids: dict[str, int] = {}
    for instrument_id in sorted_ids:
        if not caller_is_owner and _has_yfinance_data(session, instrument_id):
            blocked[str(instrument_id)] = LICENCE_RESTRICTED
            continue
        try:
            check_ids[str(instrument_id)] = assert_tradable(session, instrument_id, now)
        except ComplianceBlocked as exc:
            blocked[str(instrument_id)] = exc.reason
    run.blocked_instruments = blocked
    run.compliance_check_ids = check_ids

    compliant_ids = [i for i in sorted_ids if str(i) in check_ids]
    if not compliant_ids:
        run.status = "failed"
        run.error = "all_instruments_blocked"
        run.finished_at = now
        session.commit()
        return run

    _generate_and_persist(session, run, compliant_ids, instruments_by_id)
    run.status = "completed"
    run.finished_at = now
    session.commit()
    return run


def replay_strategy_run(session: Session, run_id: int, now: datetime) -> StrategyRun:
    """Re-executes a completed run's SIGNAL GENERATION using its stored
    compliance_check_ids / blocked_instruments -- it never calls assert_tradable
    again, so a compliance record that has since expired cannot change the
    outcome (CLAUDE.md section 7's determinism note). Produces byte-identical
    signals and config_hash to the original run, provided the underlying
    price_bars haven't changed. Creates a new strategy_runs row; `now` is
    bookkeeping only (started_at/finished_at), never fed into the hash or the
    engine."""
    original = session.get(StrategyRun, run_id)
    if original is None:
        raise StrategyRunNotFound(run_id)

    instruments_by_id: dict[int, Instrument] = {}
    for instrument_id in original.instrument_ids:
        inst = session.get(Instrument, instrument_id)
        if inst is None:
            raise UnknownInstrument(instrument_id)
        instruments_by_id[instrument_id] = inst

    config_hash = build_config_hash(
        instrument_ids=original.instrument_ids, range_start=original.range_start,
        range_end=original.range_end, fast_period=original.fast_period,
        slow_period=original.slow_period, reset_ma_each_session=original.reset_ma_each_session,
        currency=original.currency, starting_cash=original.starting_cash,
        max_position_value=original.max_position_value,
        commission_per_order=original.commission_per_order, spread_bps=original.spread_bps,
        slippage_bps=original.slippage_bps, interval=SUPPORTED_INTERVAL,
    )

    replay = StrategyRun(
        user_id=original.user_id, currency=original.currency,
        instrument_ids=original.instrument_ids, range_start=original.range_start,
        range_end=original.range_end, fast_period=original.fast_period,
        slow_period=original.slow_period, reset_ma_each_session=original.reset_ma_each_session,
        starting_cash=original.starting_cash, max_position_value=original.max_position_value,
        commission_per_order=original.commission_per_order, spread_bps=original.spread_bps,
        slippage_bps=original.slippage_bps, config_hash=config_hash, status="running",
        started_at=now, blocked_instruments=dict(original.blocked_instruments),
        compliance_check_ids=dict(original.compliance_check_ids),
    )
    session.add(replay)
    session.flush()

    compliant_ids = [i for i in original.instrument_ids if str(i) in original.compliance_check_ids]
    if not compliant_ids:
        replay.status = "failed"
        replay.error = "all_instruments_blocked"
        replay.finished_at = now
        session.commit()
        return replay

    _generate_and_persist(session, replay, compliant_ids, instruments_by_id)
    replay.status = "completed"
    replay.finished_at = now
    session.commit()
    return replay
