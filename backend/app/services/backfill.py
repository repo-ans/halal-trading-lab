"""The daily 5-minute backfill job (CLAUDE.md section 4's highest-priority action):
fetch, validate, and idempotently store bars before Yahoo's rolling 60-day intraday
window erases them for good. Reports gaps against the market calendar; never repairs
or fabricates missing bars (CLAUDE.md section 4 and 9 must-pass test 6)."""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.market_calendar import EXCHANGE_CALENDARS, session_bounds
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar
from app.providers.price.base import Bar, PriceProvider, ProviderError
from app.services.validation import (
    detect_close_indexed_days,
    expected_bar_count,
    is_forming,
    is_large_jump,
    validate_bar,
)

logger = logging.getLogger(__name__)

# Stay one day inside Yahoo's rolling 60-day 5-minute retention window (CLAUDE.md
# section 4) -- bars not captured before the window rolls are gone permanently.
LOOKBACK_DAYS = 59
INTERVAL = "5m"


@dataclass
class GapDay:
    day: date
    expected: int
    actual: int


@dataclass
class InstrumentReport:
    symbol: str
    exchange: str
    fetched: int = 0
    inserted: int = 0
    duplicate: int = 0  # already stored, identical -- no-op
    dropped_forming: int = 0
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (bar ts, reason)
    differs_kept_stored: list[str] = field(default_factory=list)  # bar ts
    large_jumps: list[str] = field(default_factory=list)  # warnings, never rejections
    suspected_close_indexed_days: list[str] = field(default_factory=list)
    error: str | None = None
    gaps: list[GapDay] = field(default_factory=list)
    # % of expected bars missing from the most recent COMPLETED session (session_close
    # <= now) -- None if no completed session was found in the scanned window. Gaps in
    # OLDER sessions are permanent and deliberately excluded: a cron checking this must
    # not stay red forever over data that can never be recovered.
    recent_session_gap_pct: Decimal | None = None


@dataclass
class BackfillReport:
    instruments: list[InstrumentReport] = field(default_factory=list)


def run_backfill(session: Session, provider: PriceProvider, instruments: list[Instrument],
                 now: datetime) -> BackfillReport:
    """One instrument's failure never blocks the others -- each gets its own
    try/except, and a failure never invents or substitutes data for that instrument,
    it just shows up as InstrumentReport.error."""
    report = BackfillReport()
    start = now - timedelta(days=LOOKBACK_DAYS)
    for instrument in instruments:
        ir = InstrumentReport(symbol=instrument.symbol, exchange=instrument.exchange)
        report.instruments.append(ir)
        try:
            _backfill_one(session, provider, instrument, start, now, ir)
        except ProviderError as exc:
            ir.error = str(exc)
            logger.error("backfill failed for %s/%s: %s",
                        instrument.symbol, instrument.exchange, exc)
    return report


def _backfill_one(session: Session, provider: PriceProvider, instrument: Instrument,
                  start: datetime, now: datetime, ir: InstrumentReport) -> None:
    calendar = EXCHANGE_CALENDARS.get(instrument.exchange)
    if calendar is None:
        ir.error = f"no market calendar mapped for exchange {instrument.exchange!r}"
        logger.error(ir.error)
        return

    ticker = provider.symbol_for(instrument.symbol, instrument.exchange)
    bars = provider.get_bars(ticker, INTERVAL, start, now)
    ir.fetched = len(bars)

    suspected_days = detect_close_indexed_days(bars, calendar)
    if suspected_days:
        ir.suspected_close_indexed_days = [d.isoformat() for d in suspected_days]
        for d in suspected_days:
            logger.error(
                "SUSPECTED CLOSE-TIME INDEXING for %s on %s: no bar at session open "
                "but one exists at session close -- the provider may be indexing by "
                "bar CLOSE, not bar OPEN as assumed throughout this pipeline",
                instrument.symbol, d)

    existing = _existing_bars(session, instrument.id, start, now)
    to_insert: list[Bar] = []
    accepted: list[Bar] = []  # passed per-bar validation, regardless of insert/differ
    for bar in bars:
        if is_forming(bar, now):
            ir.dropped_forming += 1
            continue
        bounds = session_bounds(bar.ts.date(), calendar)
        if bounds is None:
            reason = "market closed that day"
        else:
            reason = validate_bar(bar, *bounds)
        if reason is not None:
            ir.rejected.append((bar.ts.isoformat(), reason))
            logger.warning("rejected bar %s %s: %s", instrument.symbol, bar.ts, reason)
            continue
        accepted.append(bar)

        prior = existing.get(bar.ts)
        if prior is None:
            to_insert.append(bar)
        elif (prior.open, prior.high, prior.low, prior.close, prior.volume) != \
             (bar.open, bar.high, bar.low, bar.close, bar.volume):
            ir.differs_kept_stored.append(bar.ts.isoformat())
            logger.warning("refetched bar for %s %s differs from stored value; "
                           "keeping the stored value, not overwriting",
                           instrument.symbol, bar.ts)
        else:
            ir.duplicate += 1  # identical to what's already stored -- nothing to do

    if to_insert:
        session.add_all([
            PriceBar(instrument_id=instrument.id, ts=bar.ts, interval=INTERVAL,
                     open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                     volume=bar.volume, source=provider.name)
            for bar in to_insert
        ])
        session.commit()
    ir.inserted = len(to_insert)

    ir.large_jumps = _detect_large_jumps(session, instrument.id, accepted)
    for jump in ir.large_jumps:
        logger.warning("large close-to-close jump for %s: %s", instrument.symbol, jump)

    ir.gaps, ir.recent_session_gap_pct = _compute_gaps(
        session, instrument.id, start.date(), now.date(), now, calendar)


def _existing_bars(session: Session, instrument_id: int, start: datetime,
                   end: datetime) -> dict[datetime, PriceBar]:
    rows = session.scalars(
        select(PriceBar).where(
            PriceBar.instrument_id == instrument_id,
            PriceBar.interval == INTERVAL,
            PriceBar.ts >= start,
            PriceBar.ts < end,
        )
    ).all()
    return {row.ts: row for row in rows}


def _latest_prior_close(session: Session, instrument_id: int, before: datetime):
    return session.scalar(
        select(PriceBar.close).where(
            PriceBar.instrument_id == instrument_id,
            PriceBar.interval == INTERVAL,
            PriceBar.ts < before,
        ).order_by(PriceBar.ts.desc()).limit(1)
    )


def _detect_large_jumps(session: Session, instrument_id: int,
                        accepted: list[Bar]) -> list[str]:
    """A warning, never a rejection -- real stocks do gap >25% intraday on news.
    Seeded with the closest already-stored bar strictly before the first accepted bar
    (not the 59-day lookback window start -- that's almost never adjacent to real
    data), so a jump between yesterday's last bar and today's first bar is caught
    too, not just jumps within this run's freshly fetched bars."""
    if not accepted:
        return []
    ordered = sorted(accepted, key=lambda b: b.ts)
    jumps = []
    prev_close = _latest_prior_close(session, instrument_id, ordered[0].ts)
    for bar in ordered:
        if prev_close is not None and is_large_jump(prev_close, bar.close):
            pct = abs(bar.close - prev_close) / prev_close * 100
            jumps.append(f"{bar.ts.isoformat()}: {prev_close} -> {bar.close} ({pct:.1f}%)")
        prev_close = bar.close
    return jumps


def _compute_gaps(session: Session, instrument_id: int, start_day: date, end_day: date,
                  now: datetime, calendar: str) -> tuple[list[GapDay], Decimal | None]:
    """Report, don't repair (CLAUDE.md section 4): compares actual stored bar counts
    against what the market calendar says a full day should have. Also returns the gap
    percentage of the most recent COMPLETED session (ascending iteration means the
    last one found as the loop runs is the most recent) -- callers use that, not the
    full gaps list, to decide whether to treat a backfill run as failed, since older
    gaps are permanent and the full list mixes them with today's still-in-progress,
    not-actually-a-gap-yet day."""
    gaps = []
    recent_session_gap_pct: Decimal | None = None
    day = start_day
    while day <= end_day:
        bounds = session_bounds(day, calendar)
        if bounds is not None:
            expected = expected_bar_count(*bounds)
            actual = session.scalar(
                select(func.count()).select_from(PriceBar).where(
                    PriceBar.instrument_id == instrument_id,
                    PriceBar.interval == INTERVAL,
                    PriceBar.ts >= bounds[0],
                    PriceBar.ts < bounds[1],
                )
            )
            if actual != expected:
                gaps.append(GapDay(day=day, expected=expected, actual=actual))
            if bounds[1] <= now:
                recent_session_gap_pct = (
                    Decimal(0) if expected == 0
                    else Decimal(expected - actual) / Decimal(expected) * 100
                )
        day += timedelta(days=1)
    return gaps, recent_session_gap_pct
