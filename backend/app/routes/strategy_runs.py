"""Stage D: signal generation. Stage E: sizing, fills, EOD exit, cash ledger.
Stage F: metrics, paginated/filterable order history, CSV export. Any
authenticated user may run a backtest (not admin-gated); the compliance gate,
not an authorization check, is what decides which instruments actually get
signals."""
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models.instrument import Instrument
from app.models.paper import PaperFill, PaperOrder
from app.models.strategy import Signal, StrategyRun
from app.services.ai_summary import HEADING_BY_MODE
from app.services.ai_summary import PaperEngineNotRun as AiPaperEngineNotRun
from app.services.ai_summary import StrategyRunNotFound as AiStrategyRunNotFound
from app.services.ai_summary import fetch_ai_summary, generate_or_fetch_ai_summary
from app.services.csv_safety import csv_safe, numeric_cell
from app.services.metrics import compute_metrics
from app.services.paper_engine import PaperEngineAlreadyRun
from app.services.paper_engine import StrategyRunNotFound as PaperStrategyRunNotFound
from app.services.paper_engine import run_paper_engine
from app.services.rejection_messages import rejection_message
from app.services.strategy_run import (
    CurrencyMismatch,
    StrategyRunNotFound,
    UnknownInstrument,
    UnsupportedExchange,
    replay_strategy_run,
    run_strategy,
)

router = APIRouter(prefix="/strategy-runs")


def _get_owned_run(session: Session, run_id: int, user: AuthUser) -> StrategyRun:
    """Ownership is strategy_runs.user_id -- never leak whether a run exists to
    someone who doesn't own it: not-found and not-yours both return 404."""
    run = session.get(StrategyRun, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy run not found")
    return run


class StrategyRunIn(BaseModel):
    instrument_ids: list[int]
    range_start: datetime
    range_end: datetime
    fast_period: int = 5
    slow_period: int = 20
    currency: str
    starting_cash: Decimal = Decimal("1000")
    max_position_value: Decimal = Decimal("200")
    commission_per_order: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal
    reset_ma_each_session: bool = False


class StrategyRunOut(BaseModel):
    id: int
    status: str
    error: str | None
    config_hash: str
    instrument_ids: list[int]
    range_start: str
    range_end: str
    fast_period: int
    slow_period: int
    reset_ma_each_session: bool
    blocked_instruments: dict[str, str]
    compliance_check_ids: dict[str, int]
    started_at: str | None
    finished_at: str | None
    paper_status: str
    paper_completed_at: str | None
    max_position_value: str


class StrategyRunListOut(BaseModel):
    items: list[StrategyRunOut]
    total: int


class SignalOut(BaseModel):
    instrument_id: int
    signal_ts: str
    side: str
    reason: str
    ma_fast: str
    ma_slow: str
    execution_ts: str


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def _to_out(run: StrategyRun) -> StrategyRunOut:
    return StrategyRunOut(
        id=run.id, status=run.status, error=run.error, config_hash=run.config_hash,
        instrument_ids=run.instrument_ids, range_start=_iso(run.range_start),
        range_end=_iso(run.range_end), fast_period=run.fast_period,
        slow_period=run.slow_period, reset_ma_each_session=run.reset_ma_each_session,
        blocked_instruments=run.blocked_instruments, compliance_check_ids=run.compliance_check_ids,
        started_at=_iso(run.started_at), finished_at=_iso(run.finished_at),
        paper_status=run.paper_status, paper_completed_at=_iso(run.paper_completed_at),
        max_position_value=str(run.max_position_value),
    )


def _require_aware(dt: datetime, field_name: str) -> datetime:
    if dt.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{field_name} must be timezone-aware")
    return dt.astimezone(timezone.utc)


@router.post("", response_model=StrategyRunOut, status_code=status.HTTP_201_CREATED)
def create_strategy_run(
    body: StrategyRunIn,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> StrategyRunOut:
    if body.fast_period >= body.slow_period:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "fast_period must be less than slow_period")
    range_start = _require_aware(body.range_start, "range_start")
    range_end = _require_aware(body.range_end, "range_end")
    if range_end <= range_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "range_end must be after range_start")

    now = datetime.now(timezone.utc)
    try:
        run = run_strategy(
            session, user_id=user.id, instrument_ids=body.instrument_ids,
            range_start=range_start, range_end=range_end, fast_period=body.fast_period,
            slow_period=body.slow_period, currency=body.currency,
            starting_cash=body.starting_cash, max_position_value=body.max_position_value,
            commission_per_order=body.commission_per_order, spread_bps=body.spread_bps,
            slippage_bps=body.slippage_bps, reset_ma_each_session=body.reset_ma_each_session,
            now=now,
        )
    except UnknownInstrument as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"instrument {exc.instrument_id} not found")
    except CurrencyMismatch as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"instrument {exc.instrument_id} is {exc.instrument_currency}, run currency is "
            f"{exc.run_currency} -- single-currency simulation, no FX conversion")
    except UnsupportedExchange as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"instrument {exc.instrument_id}'s exchange {exc.exchange!r} has "
                            "no market calendar configured")

    if run.status == "failed" and run.error == "all_instruments_blocked":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _to_out(run).model_dump())

    return _to_out(run)


@router.get("", response_model=StrategyRunListOut)
def list_strategy_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> StrategyRunListOut:
    """Own runs only -- for the Dashboard / Virtual Trades run picker."""
    stmt = select(StrategyRun).where(StrategyRun.user_id == user.id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(
        stmt.order_by(StrategyRun.started_at.desc()).limit(limit).offset(offset)
    ).all()
    return StrategyRunListOut(items=[_to_out(r) for r in rows], total=total or 0)


@router.get("/{run_id}", response_model=StrategyRunOut)
def get_strategy_run(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> StrategyRunOut:
    run = _get_owned_run(session, run_id, user)
    return _to_out(run)


@router.get("/{run_id}/signals", response_model=list[SignalOut])
def get_strategy_run_signals(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[SignalOut]:
    _get_owned_run(session, run_id, user)

    rows = session.scalars(
        select(Signal).where(Signal.strategy_run_id == run_id)
        .order_by(Signal.instrument_id, Signal.signal_ts)
    ).all()
    return [
        SignalOut(
            instrument_id=r.instrument_id, signal_ts=_iso(r.signal_ts), side=r.side,
            reason=r.reason, ma_fast=str(r.ma_fast), ma_slow=str(r.ma_slow),
            execution_ts=_iso(r.execution_ts),
        )
        for r in rows
    ]


@router.post("/{run_id}/replay", response_model=StrategyRunOut, status_code=status.HTTP_201_CREATED)
def replay_run(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> StrategyRunOut:
    _get_owned_run(session, run_id, user)
    now = datetime.now(timezone.utc)
    try:
        run = replay_strategy_run(session, run_id, now)
    except StrategyRunNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy run not found")
    except UnknownInstrument as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"instrument {exc.instrument_id} not found")

    if run.status == "failed" and run.error == "all_instruments_blocked":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _to_out(run).model_dump())

    return _to_out(run)


class PaperRunOut(BaseModel):
    id: int
    paper_status: str
    paper_completed_at: str | None
    order_count: int
    fill_count: int


class PaperOrderOut(BaseModel):
    id: int
    instrument_id: int
    signal_id: int | None
    origin: str
    side: str
    qty: str
    intended_price: str | None
    execution_ts: str
    status: str
    rejection_reason: str | None
    rejection_message: str | None  # plain language -- Virtual Trades, CSV export


class PaperOrdersPageOut(BaseModel):
    items: list[PaperOrderOut]
    total: int


class EquityPointOut(BaseModel):
    ts: str
    cash: str


class MetricsOut(BaseModel):
    trades_count: int
    realized_pnl: str | None
    total_return: str | None
    win_rate: str | None
    avg_win: str | None
    avg_non_win: str | None
    total_fees: str
    max_drawdown: str
    unresolved_positions: int
    unresolved_instrument_ids: list[int]
    equity_curve: list[EquityPointOut]


class PaperFillOut(BaseModel):
    id: int
    order_id: int
    instrument_id: int
    side: str
    fill_ts: str
    fill_price: str
    qty: str
    commission: str
    spread_cost: str
    slippage_cost: str
    cash_balance_after: str


@router.post("/{run_id}/paper-run", response_model=PaperRunOut, status_code=status.HTTP_201_CREATED)
def paper_run(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> PaperRunOut:
    _get_owned_run(session, run_id, user)
    now = datetime.now(timezone.utc)
    try:
        run = run_paper_engine(session, run_id, now)
    except PaperStrategyRunNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy run not found")
    except PaperEngineAlreadyRun:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the paper engine has already run for this strategy run")

    order_count = session.scalar(
        select(func.count()).select_from(PaperOrder).where(PaperOrder.strategy_run_id == run_id))
    fill_count = session.scalar(
        select(func.count()).select_from(PaperFill).join(PaperOrder)
        .where(PaperOrder.strategy_run_id == run_id))
    return PaperRunOut(
        id=run.id, paper_status=run.paper_status, paper_completed_at=_iso(run.paper_completed_at),
        order_count=order_count, fill_count=fill_count,
    )


def _order_out(r: PaperOrder, max_position_value: Decimal) -> PaperOrderOut:
    return PaperOrderOut(
        id=r.id, instrument_id=r.instrument_id, signal_id=r.signal_id, origin=r.origin,
        side=r.side, qty=str(r.qty),
        intended_price=None if r.intended_price is None else str(r.intended_price),
        execution_ts=_iso(r.execution_ts), status=r.status, rejection_reason=r.rejection_reason,
        rejection_message=rejection_message(r.rejection_reason, intended_price=r.intended_price,
                                            max_position_value=max_position_value),
    )


@router.get("/{run_id}/orders", response_model=PaperOrdersPageOut)
def get_paper_orders(
    run_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    instrument_id: int | None = None,
    status_: str | None = Query(None, alias="status"),
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> PaperOrdersPageOut:
    run = _get_owned_run(session, run_id, user)
    stmt = select(PaperOrder).where(PaperOrder.strategy_run_id == run_id)
    if instrument_id is not None:
        stmt = stmt.where(PaperOrder.instrument_id == instrument_id)
    if status_ is not None:
        stmt = stmt.where(PaperOrder.status == status_)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(
        stmt.order_by(PaperOrder.execution_ts, PaperOrder.instrument_id).limit(limit).offset(offset)
    ).all()
    return PaperOrdersPageOut(
        items=[_order_out(r, run.max_position_value) for r in rows], total=total or 0)


@router.get("/{run_id}/metrics", response_model=MetricsOut)
def get_metrics(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> MetricsOut:
    run = _get_owned_run(session, run_id, user)
    if run.paper_status != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the paper engine has not run yet for this strategy run")

    m = compute_metrics(session, run)
    return MetricsOut(
        trades_count=m.trades_count,
        realized_pnl=None if m.realized_pnl is None else str(m.realized_pnl),
        total_return=None if m.total_return is None else str(m.total_return),
        win_rate=None if m.win_rate is None else str(m.win_rate),
        avg_win=None if m.avg_win is None else str(m.avg_win),
        avg_non_win=None if m.avg_non_win is None else str(m.avg_non_win),
        total_fees=str(m.total_fees), max_drawdown=str(m.max_drawdown),
        unresolved_positions=m.unresolved_positions,
        unresolved_instrument_ids=m.unresolved_instrument_ids,
        equity_curve=[EquityPointOut(ts=_iso(ts), cash=str(cash)) for ts, cash in m.equity_curve],
    )


def _csv_response(filename: str, header: list[str], rows: list[list[str]]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue().encode("utf-8"), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/orders.csv")
def get_paper_orders_csv(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    run = _get_owned_run(session, run_id, user)
    rows = session.execute(
        select(PaperOrder, Signal.signal_ts, Instrument.symbol)
        .join(Signal, PaperOrder.signal_id == Signal.id, isouter=True)
        .join(Instrument, PaperOrder.instrument_id == Instrument.id)
        .where(PaperOrder.strategy_run_id == run_id)
        .order_by(PaperOrder.execution_ts, PaperOrder.instrument_id)
    ).all()

    csv_rows = []
    for order, signal_ts, symbol in rows:
        msg = rejection_message(order.rejection_reason, intended_price=order.intended_price,
                                max_position_value=run.max_position_value)
        csv_rows.append([
            numeric_cell(str(run_id)), csv_safe(symbol), _iso(order.execution_ts), order.side,
            order.origin, numeric_cell(str(order.qty)), order.status, order.rejection_reason or "",
            csv_safe(msg or ""), _iso(signal_ts) if signal_ts else "",
        ])
    return _csv_response(
        f"paper_orders_{run_id}.csv",
        ["run_id", "instrument_symbol", "execution_ts", "side", "origin", "qty", "status",
         "rejection_reason", "rejection_message", "signal_ts"],
        csv_rows,
    )


@router.get("/{run_id}/fills.csv")
def get_paper_fills_csv(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    run = _get_owned_run(session, run_id, user)
    rows = session.execute(
        select(PaperFill, PaperOrder.side, Instrument.symbol, PaperOrder.intended_price)
        .join(PaperOrder, PaperFill.order_id == PaperOrder.id)
        .join(Instrument, PaperOrder.instrument_id == Instrument.id)
        .where(PaperOrder.strategy_run_id == run_id)
        .order_by(PaperFill.fill_ts, PaperOrder.instrument_id)
    ).all()

    csv_rows = []
    for fill, side, symbol, raw_open in rows:
        csv_rows.append([
            numeric_cell(str(run_id)), csv_safe(symbol), _iso(fill.fill_ts), side,
            numeric_cell(str(fill.qty)), numeric_cell(str(raw_open)) if raw_open is not None else "",
            numeric_cell(str(fill.fill_price)), numeric_cell(str(fill.spread_cost)),
            numeric_cell(str(fill.slippage_cost)), numeric_cell(str(fill.commission)),
            numeric_cell(str(fill.cash_balance_after)),
        ])
    return _csv_response(
        f"paper_fills_{run_id}.csv",
        ["run_id", "instrument_symbol", "ts", "side", "qty", "raw_open", "fill_price",
         "spread_cost", "slippage_cost", "commission", "cash_balance_after"],
        csv_rows,
    )


@router.get("/{run_id}/fills", response_model=list[PaperFillOut])
def get_paper_fills(
    run_id: int,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[PaperFillOut]:
    _get_owned_run(session, run_id, user)
    rows = session.execute(
        select(PaperFill, PaperOrder.instrument_id, PaperOrder.side)
        .join(PaperOrder, PaperFill.order_id == PaperOrder.id)
        .where(PaperOrder.strategy_run_id == run_id)
        .order_by(PaperFill.fill_ts, PaperOrder.instrument_id)
    ).all()
    return [
        PaperFillOut(
            id=fill.id, order_id=fill.order_id, instrument_id=instrument_id, side=side,
            fill_ts=_iso(fill.fill_ts), fill_price=str(fill.fill_price), qty=str(fill.qty),
            commission=str(fill.commission), spread_cost=str(fill.spread_cost),
            slippage_cost=str(fill.slippage_cost), cash_balance_after=str(fill.cash_balance_after),
        )
        for fill, instrument_id, side in rows
    ]


class AiSummaryIn(BaseModel):
    mode: Literal["template", "llm"] = "template"


class AiSummaryOut(BaseModel):
    id: int
    mode: str  # what was actually rendered -- drives the heading
    requested_mode: str  # what the caller asked for
    heading: str
    report_text: str
    llm_attempts: int
    generated_at: str
    fallback_occurred: bool  # true only when "llm" was requested but mode != "llm"


def _ai_summary_out(report) -> AiSummaryOut:
    return AiSummaryOut(
        id=report.id, mode=report.mode, requested_mode=report.requested_mode,
        heading=HEADING_BY_MODE[report.mode], report_text=report.report_text,
        llm_attempts=report.llm_attempts, generated_at=_iso(report.generated_at),
        fallback_occurred=(report.requested_mode == "llm" and report.mode != "llm"),
    )


@router.post("/{run_id}/ai-summary", response_model=AiSummaryOut, status_code=status.HTTP_201_CREATED)
def post_ai_summary(
    run_id: int,
    body: AiSummaryIn,
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> AiSummaryOut:
    _get_owned_run(session, run_id, user)
    now = datetime.now(timezone.utc)
    try:
        report = generate_or_fetch_ai_summary(session, run_id, user.id, body.mode, now)
    except AiStrategyRunNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy run not found")
    except AiPaperEngineNotRun:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the paper engine has not run yet for this strategy run")
    return _ai_summary_out(report)


@router.get("/{run_id}/ai-summary", response_model=AiSummaryOut)
def get_ai_summary(
    run_id: int,
    mode: Literal["template", "llm"] = "template",
    user: AuthUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> AiSummaryOut:
    _get_owned_run(session, run_id, user)
    report = fetch_ai_summary(session, run_id, mode)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no ai-summary generated yet for this mode")
    return _ai_summary_out(report)
