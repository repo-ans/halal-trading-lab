"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1) -- same pattern as test_paper_engine_service.py. The
concurrency test is the one exception: two REAL, independently-committing
connections, same reasoning as Stage E's own concurrency test. The LLM call
itself is always mocked -- no real network call in any test -- but nothing
about the database locking is."""
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.services.ai_summary as ai_summary
from app.config import get_settings
from app.market_calendar import session_bar_opens
from app.models.ai_report import AiReport
from app.models.instrument import Instrument
from app.models.paper import PaperOrder
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.models.strategy import Signal, StrategyRun
from app.services.ai_summary import (
    HEADING_BY_MODE,
    PaperEngineNotRun,
    StrategyRunNotFound,
    generate_or_fetch_ai_summary,
)
from app.services.ai_summary_llm import LlmCallError
from app.services.compliance import create_compliance_check
from app.services.paper_engine import run_paper_engine
from app.services.strategy_run import run_strategy
from tests.conftest import run_db_tests

CAL = "NYSE"
DAY = date(2026, 12, 15)
COSTS = dict(commission_per_order=Decimal("1.00"), spread_bps=Decimal("2"), slippage_bps=Decimal("1"))


def _instrument(session, symbol="ZAISUM") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency="USD", name="x")
    session.add(inst)
    session.flush()
    return inst


def _insert_bars(session, instrument_id: int, closes: list[int]) -> list[datetime]:
    opens = session_bar_opens(DAY, CAL)[:len(closes)]
    session.add_all([
        PriceBar(instrument_id=instrument_id, ts=t, interval=SUPPORTED_INTERVAL,
                 open=Decimal(c), high=Decimal(c), low=Decimal(c), close=Decimal(c),
                 volume=100, source="manual")
        for t, c in zip(opens, closes)
    ])
    session.flush()
    return opens


def _make_compliant(session, instrument_id: int, now: datetime, notes=None) -> None:
    create_compliance_check(
        session, instrument_id=instrument_id, status="COMPLIANT", source="manual",
        checked_at=now - timedelta(days=1), expires_at=now + timedelta(days=90),
        created_by=None, evidence_url="https://example.com", notes=notes,
    )
    session.flush()


def _ready_run(session, symbol, closes, now) -> StrategyRun:
    inst = _instrument(session, symbol=symbol)
    _insert_bars(session, inst.id, closes)
    # notes deliberately look like a demo fixture: the concurrency test below
    # uses a REAL, non-rolled-back connection, and compliance_checks is
    # append-only (the row can never be deleted) -- db_test also doubles as
    # the stand-in "target" database in test_demo_mode.py's non-demo-data
    # guard tests, so an un-tagged row here would permanently trip that guard
    # for the rest of this container's lifetime (this exact bug already bit
    # test_paper_engine_service.py's own concurrency test once).
    _make_compliant(session, inst.id, now, notes=(
        "DEMO FIXTURE, NOT A RULING -- not a real demo seed, a Stage G "
        "ai-summary test leftover kept demo-tagged because this table is "
        "append-only"))
    session.commit()
    opens = session_bar_opens(DAY, CAL)
    run = run_strategy(
        session, user_id=uuid4(), instrument_ids=[inst.id], range_start=opens[0],
        range_end=opens[len(closes) - 1] + timedelta(minutes=5), fast_period=2,
        slow_period=3, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("1000"), reset_ma_each_session=False, now=now, **COSTS,
    )
    assert run.status == "completed"
    run_paper_engine(session, run.id, now)
    return run


NOW = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
CLOSES = [10, 10, 10, 10, 20, 30, 5, 5, 5, 5]


@run_db_tests
def test_template_mode_renders_and_stores(db_conn):
    run = _ready_run(db_conn, "ZAISUMT1", CLOSES, NOW)
    report = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "template", NOW)
    assert report.mode == "template"
    assert report.requested_mode == "template"
    assert report.llm_attempts == 0
    assert "trade" in report.report_text


@run_db_tests
def test_repeat_template_request_returns_stored_row_unchanged(db_conn):
    run = _ready_run(db_conn, "ZAISUMT2", CLOSES, NOW)
    first = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "template", NOW)
    later = NOW + timedelta(hours=1)
    second = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "template", later)
    assert second.id == first.id
    assert second.generated_at == first.generated_at


@run_db_tests
def test_not_run_paper_engine_raises(db_conn):
    inst = _instrument(db_conn, symbol="ZAISUMNR")
    _insert_bars(db_conn, inst.id, CLOSES)
    _make_compliant(db_conn, inst.id, NOW)
    db_conn.commit()
    opens = session_bar_opens(DAY, CAL)
    run = run_strategy(
        db_conn, user_id=uuid4(), instrument_ids=[inst.id], range_start=opens[0],
        range_end=opens[len(CLOSES) - 1] + timedelta(minutes=5), fast_period=2, slow_period=3,
        currency="USD", starting_cash=Decimal("1000"), max_position_value=Decimal("1000"),
        reset_ma_each_session=False, now=NOW, **COSTS,
    )
    with pytest.raises(PaperEngineNotRun):
        generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "template", NOW)


@run_db_tests
def test_unknown_run_raises(db_conn):
    with pytest.raises(StrategyRunNotFound):
        generate_or_fetch_ai_summary(db_conn, 999999, uuid4(), "template", NOW)


@run_db_tests
def test_llm_success_stores_mode_llm(db_conn, monkeypatch):
    run = _ready_run(db_conn, "ZAISUML1", CLOSES, NOW)
    monkeypatch.setattr(get_settings(), "ai_api_key", SecretStr("fake-key"))
    monkeypatch.setattr(ai_summary, "call_llm", lambda **kw: "A plain summary with {trades_count} trades.")

    report = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "llm", NOW)
    assert report.mode == "llm"
    assert report.requested_mode == "llm"
    assert report.llm_attempts == 1
    assert HEADING_BY_MODE["llm"] != HEADING_BY_MODE["template"]


@run_db_tests
def test_llm_failure_falls_back_never_labeled_llm(db_conn, monkeypatch):
    run = _ready_run(db_conn, "ZAISUML2", CLOSES, NOW)
    monkeypatch.setattr(get_settings(), "ai_api_key", SecretStr("fake-key"))

    def _always_fail(**kw):
        raise LlmCallError("simulated failure")
    monkeypatch.setattr(ai_summary, "call_llm", _always_fail)

    report = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "llm", NOW)
    assert report.mode == "template"  # NEVER "llm" when it fell back
    assert report.requested_mode == "llm"
    assert report.llm_attempts == 1


@run_db_tests
def test_llm_guard_rejection_falls_back(db_conn, monkeypatch):
    run = _ready_run(db_conn, "ZAISUML3", CLOSES, NOW)
    monkeypatch.setattr(get_settings(), "ai_api_key", SecretStr("fake-key"))
    monkeypatch.setattr(ai_summary, "call_llm", lambda **kw: "This run made a guaranteed profit.")

    report = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "llm", NOW)
    assert report.mode == "template"


@run_db_tests
def test_llm_attempts_capped_at_three(db_conn, monkeypatch):
    run = _ready_run(db_conn, "ZAISUML4", CLOSES, NOW)
    monkeypatch.setattr(get_settings(), "ai_api_key", SecretStr("fake-key"))
    calls = {"count": 0}

    def _always_fail(**kw):
        calls["count"] += 1
        raise LlmCallError("simulated failure")
    monkeypatch.setattr(ai_summary, "call_llm", _always_fail)

    for _ in range(4):
        report = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "llm", NOW)
    assert report.llm_attempts == 3  # the 4th request did not attempt again
    assert calls["count"] == 3 * ai_summary.LLM_CALL_ATTEMPTS_PER_REQUEST


@run_db_tests
def test_daily_cap_blocks_without_attempting(db_conn, monkeypatch):
    user_id = uuid4()
    run1 = _ready_run(db_conn, "ZAISUMD1", CLOSES, NOW)
    db_conn.execute(sa.update(StrategyRun).where(StrategyRun.id == run1.id).values(user_id=user_id))
    run2 = _ready_run(db_conn, "ZAISUMD2", CLOSES, NOW)
    db_conn.execute(sa.update(StrategyRun).where(StrategyRun.id == run2.id).values(user_id=user_id))
    db_conn.commit()

    monkeypatch.setattr(get_settings(), "ai_api_key", SecretStr("fake-key"))
    monkeypatch.setattr(get_settings(), "ai_summary_daily_cap", 1)
    calls = {"count": 0}

    def _fail(**kw):
        calls["count"] += 1
        raise LlmCallError("simulated failure")
    monkeypatch.setattr(ai_summary, "call_llm", _fail)

    generate_or_fetch_ai_summary(db_conn, run1.id, user_id, "llm", NOW)
    assert calls["count"] == ai_summary.LLM_CALL_ATTEMPTS_PER_REQUEST

    report2 = generate_or_fetch_ai_summary(db_conn, run2.id, user_id, "llm", NOW)
    assert report2.mode == "template"
    assert report2.llm_attempts == 0  # cap hit before any attempt was made
    assert calls["count"] == ai_summary.LLM_CALL_ATTEMPTS_PER_REQUEST  # unchanged -- no second attempt


@run_db_tests
def test_unique_constraint_on_run_and_requested_mode(db_conn):
    run = _ready_run(db_conn, "ZAISUMU1", CLOSES, NOW)
    report = generate_or_fetch_ai_summary(db_conn, run.id, run.user_id, "template", NOW)
    db_conn.commit()

    dup = AiReport(
        strategy_run_id=run.id, requested_mode="template", mode="template",
        report_text="x", metrics={}, model=None, model_version=None,
        input_hash="x", llm_attempts=0, prompt_version="x",
    )
    db_conn.add(dup)
    with pytest.raises(IntegrityError):
        db_conn.flush()


@run_db_tests
def test_concurrent_llm_requests_cause_exactly_one_call(live_db_url, monkeypatch):
    setup_engine = sa.create_engine(live_db_url)
    symbol = f"ZAISUMCONC{uuid4().hex[:8].upper()}"
    run_id = None
    user_id = uuid4()
    try:
        with Session(setup_engine) as setup:
            run = _ready_run(setup, symbol, CLOSES, NOW)
            setup.execute(sa.update(StrategyRun).where(StrategyRun.id == run.id).values(user_id=user_id))
            setup.commit()
            run_id = run.id

        monkeypatch.setattr(get_settings(), "ai_api_key", SecretStr("fake-key"))
        call_count = {"n": 0}
        lock = threading.Lock()

        def slow_call_llm(**kw):
            with lock:
                call_count["n"] += 1
            time.sleep(0.4)
            return "A plain summary with {trades_count} trades."

        monkeypatch.setattr(ai_summary, "call_llm", slow_call_llm)

        outcomes: dict[str, str] = {}

        def call_it(key: str) -> None:
            engine = sa.create_engine(live_db_url)
            try:
                with Session(engine) as s:
                    report = generate_or_fetch_ai_summary(s, run_id, user_id, "llm", NOW)
                    outcomes[key] = report.mode
            finally:
                engine.dispose()

        t1 = threading.Thread(target=call_it, args=("a",))
        t2 = threading.Thread(target=call_it, args=("b",))
        t1.start()
        time.sleep(0.1)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert outcomes == {"a": "llm", "b": "llm"}  # both see the same successful result
        assert call_count["n"] == 1  # the lock meant only ONE caller ever actually called the LLM

        with Session(setup_engine) as check:
            rows = check.scalars(
                sa.select(AiReport).where(AiReport.strategy_run_id == run_id)).all()
            assert len(rows) == 1
            assert rows[0].llm_attempts == 1
    finally:
        if run_id is not None:
            with Session(setup_engine) as cleanup:
                cleanup.execute(sa.delete(AiReport).where(AiReport.strategy_run_id == run_id))
                cleanup.execute(sa.delete(PaperOrder).where(PaperOrder.strategy_run_id == run_id))
                cleanup.execute(sa.delete(Signal).where(Signal.strategy_run_id == run_id))
                cleanup.execute(sa.delete(StrategyRun).where(StrategyRun.id == run_id))
                cleanup.execute(sa.delete(PriceBar).where(
                    PriceBar.instrument_id == sa.select(Instrument.id)
                    .where(Instrument.symbol == symbol).scalar_subquery()))
                cleanup.commit()
        setup_engine.dispose()
