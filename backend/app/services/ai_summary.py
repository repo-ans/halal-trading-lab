"""Orchestrates Stage G: locks the run row, checks/creates the one
(strategy_run_id, requested_mode) row, tries the LLM (with a timeout, one
retry, and both a per-row attempts cap and a per-user daily cap) or falls
straight to the template, runs the output guards, substitutes placeholders,
appends the fixed limitation/disclaimer text, stores, and returns.

`mode` on the stored row is always what was ACTUALLY rendered -- a fallback is
never labelled 'llm', even when 'llm' was what was requested."""
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.ai_report import AiReport
from app.models.paper import PaperOrder
from app.models.strategy import StrategyRun
from app.services.ai_summary_guards import validate_raw_generated_text
from app.services.ai_summary_input import ALLOWED_PLACEHOLDERS, build_ai_summary_input, compute_values_hash
from app.services.ai_summary_llm import LlmCallError, call_llm
from app.services.ai_summary_template import PROMPT_VERSION, render_raw_template
from app.services.metrics import compute_metrics

MAX_LLM_ATTEMPTS_PER_ROW = 3
LLM_CALL_ATTEMPTS_PER_REQUEST = 2  # initial + one retry, per the plan's "a timeout, one retry"

HEADING_BY_MODE = {
    "template": "Plain-language summary of a simulated result (automatic, no AI)",
    "llm": "AI-generated explanation of a simulated result",
}

LIMITATION_TEXT = (
    "This reflects realized trades only -- it does not include gains or losses "
    "while a position was still open."
)
DISCLAIMER_TEXT = (
    "Paper trading only. Not financial advice. Halal status comes from a third "
    "party and is not our own ruling."
)


class StrategyRunNotFound(Exception):
    def __init__(self, run_id: int):
        self.run_id = run_id


class PaperEngineNotRun(Exception):
    def __init__(self, run_id: int):
        self.run_id = run_id


def _rejection_counts(session: Session, run_id: int) -> dict[str, int]:
    rows = session.execute(
        select(PaperOrder.rejection_reason, func.count())
        .where(PaperOrder.strategy_run_id == run_id, PaperOrder.rejection_reason.is_not(None))
        .group_by(PaperOrder.rejection_reason)
    ).all()
    return dict(rows)


def _daily_attempt_count(session: Session, user_id, now: datetime) -> int:
    """Every LLM attempt (success, failure, or guard rejection) within the last
    rolling 24h, for this user, across every one of their runs. `generated_at`
    is updated on every attempt (not just first creation), so this is a
    last-touched window, not a first-created one -- deliberately: it means an
    old row retried today has ALL its attempts (including ones from days ago)
    counted against today's cap, which makes this soft cap MORE conservative
    on a retry, never less. A hard security boundary this is not; a reasonable
    bound on surprise spend it is."""
    total = session.scalar(
        select(func.coalesce(func.sum(AiReport.llm_attempts), 0))
        .select_from(AiReport)
        .join(StrategyRun, AiReport.strategy_run_id == StrategyRun.id)
        .where(StrategyRun.user_id == user_id, AiReport.generated_at >= now - timedelta(hours=24))
    )
    return total or 0


def _build_report_text(generated_portion: str) -> str:
    return f"{generated_portion}\n\n{LIMITATION_TEXT} {DISCLAIMER_TEXT}"


def _call_llm_with_retry(settings) -> str | None:
    for _ in range(LLM_CALL_ATTEMPTS_PER_REQUEST):
        try:
            return call_llm(
                api_key=settings.ai_api_key.get_secret_value(), model=settings.ai_model,
                timeout_seconds=settings.ai_summary_timeout_seconds,
                allowed_placeholders=ALLOWED_PLACEHOLDERS,
            )
        except LlmCallError:
            continue
    return None


def _upsert(
    session: Session, existing: AiReport | None, *, run_id: int, requested_mode: str, mode: str,
    report_text: str, metrics_snapshot: dict, model: str | None, model_version: str | None,
    input_hash: str, llm_attempts: int, now: datetime,
) -> AiReport:
    if existing is not None:
        existing.mode = mode
        existing.report_text = report_text
        existing.metrics = metrics_snapshot
        existing.model = model
        existing.model_version = model_version
        existing.input_hash = input_hash
        existing.llm_attempts = llm_attempts
        existing.prompt_version = PROMPT_VERSION
        existing.generated_at = now
        session.flush()
        return existing
    report = AiReport(
        strategy_run_id=run_id, requested_mode=requested_mode, mode=mode, report_text=report_text,
        metrics=metrics_snapshot, model=model, model_version=model_version, input_hash=input_hash,
        llm_attempts=llm_attempts, prompt_version=PROMPT_VERSION, generated_at=now,
    )
    session.add(report)
    session.flush()
    return report


def _render_template(session: Session, run: StrategyRun) -> tuple[str, dict]:
    raw = render_raw_template()
    rejection = validate_raw_generated_text(raw)
    assert rejection is None, f"the fixed template failed its own guard: {rejection}"
    ai_input = build_ai_summary_input(run, compute_metrics(session, run), _rejection_counts(session, run.id))
    body = raw.format(**ai_input.values)
    return _build_report_text(body), ai_input.values


def _render_and_store_template(
    session: Session, run: StrategyRun, requested_mode: str, now: datetime, existing: AiReport | None,
) -> AiReport:
    report_text, values = _render_template(session, run)
    report = _upsert(
        session, existing, run_id=run.id, requested_mode=requested_mode, mode="template",
        report_text=report_text, metrics_snapshot=values, model=None, model_version=None,
        input_hash=compute_values_hash(values), llm_attempts=existing.llm_attempts if existing else 0,
        now=now,
    )
    session.commit()
    return report


def _attempt_llm_and_store(session: Session, run: StrategyRun, existing: AiReport | None, now: datetime) -> AiReport:
    settings = get_settings()
    ai_input = build_ai_summary_input(run, compute_metrics(session, run), _rejection_counts(session, run.id))
    attempts = (existing.llm_attempts if existing else 0) + 1

    raw = _call_llm_with_retry(settings)
    rejection = validate_raw_generated_text(raw) if raw is not None else "llm call failed"

    if raw is not None and rejection is None:
        body = raw.format(**ai_input.values)
        mode, model, model_version = "llm", settings.ai_model, None
    else:
        template_raw = render_raw_template()
        body = template_raw.format(**ai_input.values)
        mode, model, model_version = "template", None, None

    report_text = _build_report_text(body)
    report = _upsert(
        session, existing, run_id=run.id, requested_mode="llm", mode=mode, report_text=report_text,
        metrics_snapshot=ai_input.values, model=model, model_version=model_version,
        input_hash=ai_input.input_hash(), llm_attempts=attempts, now=now,
    )
    session.commit()
    return report


def generate_or_fetch_ai_summary(
    session: Session, run_id: int, user_id, requested_mode: str, now: datetime,
) -> AiReport:
    run = session.execute(
        select(StrategyRun).where(StrategyRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise StrategyRunNotFound(run_id)
    if run.paper_status != "completed":
        raise PaperEngineNotRun(run_id)

    existing = session.scalar(
        select(AiReport).where(
            AiReport.strategy_run_id == run_id, AiReport.requested_mode == requested_mode)
    )

    if existing is not None and existing.mode == "llm":
        return existing  # a full, successful LLM result already exists -- never regenerate

    if requested_mode == "template":
        if existing is not None:
            return existing
        return _render_and_store_template(session, run, requested_mode, now, None)

    # requested_mode == "llm" from here on -- existing, if any, has mode="template" (a prior fallback)
    settings = get_settings()
    has_key = settings.ai_api_key.get_secret_value() != ""
    attempts_so_far = existing.llm_attempts if existing else 0
    under_attempts_cap = attempts_so_far < MAX_LLM_ATTEMPTS_PER_ROW
    under_daily_cap = _daily_attempt_count(session, user_id, now) < settings.ai_summary_daily_cap

    if not has_key or not under_attempts_cap or not under_daily_cap:
        if existing is not None:
            return existing
        return _render_and_store_template(session, run, requested_mode, now, None)

    return _attempt_llm_and_store(session, run, existing, now)


def fetch_ai_summary(session: Session, run_id: int, requested_mode: str) -> AiReport | None:
    return session.scalar(
        select(AiReport).where(
            AiReport.strategy_run_id == run_id, AiReport.requested_mode == requested_mode)
    )
