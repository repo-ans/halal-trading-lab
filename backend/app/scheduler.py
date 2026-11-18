"""APScheduler: the in-process half of CLAUDE.md's chosen scheduler architecture
(APScheduler in-process + GitHub Actions cron as the external trigger, which also
wakes a sleeping Render free-tier container). Runs the same backfill job on startup
and once a day; POST /jobs/backfill (app/main.py) runs the identical function so both
triggers share one code path."""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar
from app.providers.price.base import ProviderError
from app.providers.price.yfinance_provider import YFinanceProvider
from app.seed import seed_instruments
from app.services.backfill import BackfillReport, run_backfill

logger = logging.getLogger(__name__)

# Well after any US or TSX market close, including summer/winter DST (close is
# 20:00-21:00 UTC depending on DST) -- a generous buffer, not a precise finalization
# calculation. Revisit if a provider with documented finalization timing (e.g. EODHD,
# "~2-3 hours after close" per CLAUDE.md) replaces yfinance.
DAILY_BACKFILL_HOUR_UTC = 2

# Guards the STARTUP trigger only (not the daily cron, which only ever fires once a
# day anyway). Without this, every uvicorn --reload hot-reload or container restart
# would re-hit Yahoo for the full lookback window -- wasteful, and risks rate limits
# on a free, unauthenticated API we don't control.
STARTUP_GUARD_INTERVAL = timedelta(hours=6)


def _recently_backfilled(session: Session, provider_name: str, now: datetime,
                         within: timedelta = STARTUP_GUARD_INTERVAL) -> bool:
    """Checked against price_bars.ingested_at (not in-memory state), so the guard
    survives a process restart -- the exact case it exists to protect against."""
    last = session.scalar(
        select(func.max(PriceBar.ingested_at)).where(PriceBar.source == provider_name)
    )
    return last is not None and (now - last) < within


def run_backfill_job(*, startup_guard: bool = False) -> BackfillReport | None:
    """Seeds instruments, then backfills every active one. Dev-only data source: if
    ALLOW_DEV_DATA_SOURCE isn't set, this logs and returns None rather than crashing
    the scheduler -- a misconfigured provider must never take the whole app down."""
    try:
        provider = YFinanceProvider()
    except ProviderError as exc:
        logger.error("backfill job skipped: %s", exc)
        return None

    with session_scope() as session:
        now = datetime.now(timezone.utc)
        if startup_guard and _recently_backfilled(session, provider.name, now):
            logger.info("startup backfill skipped: ran within the last %s",
                       STARTUP_GUARD_INTERVAL)
            return None

        seed_instruments(session)
        instruments = session.scalars(
            select(Instrument).where(Instrument.active.is_(True))
        ).all()
        report = run_backfill(session, provider, instruments, now)

    for ir in report.instruments:
        logger.info(
            "backfill %s/%s: fetched=%d inserted=%d rejected=%d dropped_forming=%d "
            "differs_kept=%d gaps=%d error=%s",
            ir.symbol, ir.exchange, ir.fetched, ir.inserted, len(ir.rejected),
            ir.dropped_forming, len(ir.differs_kept_stored), len(ir.gaps), ir.error,
        )
    return report


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_backfill_job,
        CronTrigger(hour=DAILY_BACKFILL_HOUR_UTC, minute=0),
        id="daily_backfill",
        replace_existing=True,
    )
    return scheduler
