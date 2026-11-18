import asyncio
import hmac
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from decimal import Decimal

from app.auth import AuthUser, current_user
from app.config import get_settings
from app.scheduler import create_scheduler, run_backfill_job
from app.services.backfill import BackfillReport

# Gaps in HISTORICAL sessions are permanent and must never keep a cron red forever;
# only the most recent COMPLETED session's gap ratio decides pass/fail here (see
# InstrumentReport.recent_session_gap_pct).
RECENT_GAP_FAIL_THRESHOLD = Decimal("10")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = create_scheduler()
    scheduler.start()
    # Fire-and-forget: run once on startup without blocking the app from serving
    # /health while a multi-symbol, multi-week fetch is in flight. startup_guard=True
    # skips it if a backfill already ran in the last 6 hours (see scheduler.py) --
    # otherwise every restart would re-hit Yahoo for the full lookback window.
    asyncio.create_task(asyncio.to_thread(run_backfill_job, startup_guard=True))
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Halal Trading Lab", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/me")
def me(user: AuthUser = Depends(current_user)) -> dict:
    return {"id": str(user.id), "email": user.email}


def get_backfill_runner() -> Callable[[], BackfillReport | None]:
    return run_backfill_job


def _report_to_dict(report: BackfillReport) -> dict:
    return {
        "instruments": [
            {
                "symbol": ir.symbol,
                "exchange": ir.exchange,
                "fetched": ir.fetched,
                "inserted": ir.inserted,
                "duplicate": ir.duplicate,
                "dropped_forming": ir.dropped_forming,
                "rejected": len(ir.rejected),
                "differs_kept_stored": len(ir.differs_kept_stored),
                "large_jumps": ir.large_jumps,
                "suspected_close_indexed_days": ir.suspected_close_indexed_days,
                "gaps": [{"day": g.day.isoformat(), "expected": g.expected, "actual": g.actual}
                        for g in ir.gaps],
                "recent_session_gap_pct": (
                    str(ir.recent_session_gap_pct)
                    if ir.recent_session_gap_pct is not None else None),
                "error": ir.error,
            }
            for ir in report.instruments
        ]
    }


@app.post("/jobs/backfill")
def trigger_backfill(
    x_job_secret: str | None = Header(default=None),
    runner: Callable[[], BackfillReport | None] = Depends(get_backfill_runner),
) -> JSONResponse:
    secret = get_settings().job_secret
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "JOB_SECRET is not configured")
    if not hmac.compare_digest(x_job_secret or "", secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing job secret")

    report = runner()
    if report is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "dev data source not enabled (ALLOW_DEV_DATA_SOURCE)")

    # 502, not 207 -- 207 is a 2xx, and curl --fail / GitHub Actions treat any 2xx as
    # success, so a cron hitting this endpoint would stay green through a real upstream
    # failure. 207's "partial success" body shape (one subrequest status per item) is
    # still what we return, just under a status code that actually fails the caller.
    any_fetch_failed = any(ir.error is not None for ir in report.instruments)
    recent_gap_too_large = any(
        ir.recent_session_gap_pct is not None
        and ir.recent_session_gap_pct > RECENT_GAP_FAIL_THRESHOLD
        for ir in report.instruments
    )
    status_code = (status.HTTP_502_BAD_GATEWAY
                   if (any_fetch_failed or recent_gap_too_large)
                   else status.HTTP_200_OK)
    return JSONResponse(content=_report_to_dict(report), status_code=status_code)
