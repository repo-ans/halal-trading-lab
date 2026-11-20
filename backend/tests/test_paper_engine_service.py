"""DB-writing: real inserts against the local test database (db_conn, db_test,
RUN_DB_TESTS=1) -- same pattern as test_strategy_run_service.py. The concurrency
test is the one exception: it needs two REAL, independently-committing
connections to prove the row lock actually serializes two callers, so it uses
`live_db_url` directly rather than the savepoint-rollback `db_conn` fixture, and
cleans up its own rows explicitly."""
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
import pytest
from sqlalchemy.orm import Session

from app.market_calendar import session_bar_opens
from app.models.instrument import Instrument
from app.models.paper import PaperFill, PaperOrder
from app.models.price_bar import SUPPORTED_INTERVAL, PriceBar
from app.models.strategy import Signal, StrategyRun
from app.services.compliance import create_compliance_check
from app.services.strategy_run import run_strategy
import app.services.paper_engine as paper_engine
from app.services.paper_engine import EngineCurrencyMismatch, PaperEngineAlreadyRun, run_paper_engine
from tests.conftest import run_db_tests

CAL = "NYSE"
DAY = date(2026, 12, 15)
COSTS = dict(commission_per_order=Decimal("1.00"), spread_bps=Decimal("2"), slippage_bps=Decimal("1"))


def _instrument(session, symbol="ZPAPER", currency="USD") -> Instrument:
    inst = Instrument(symbol=symbol, exchange="XNAS", currency=currency, name="x")
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


def _make_compliant(session, instrument_id: int, now: datetime, notes: str | None = None) -> None:
    create_compliance_check(
        session, instrument_id=instrument_id, status="halal", source="manual",
        checked_at=now - timedelta(days=1), expires_at=now + timedelta(days=90),
        created_by=None, evidence_url="https://example.com", notes=notes,
    )
    session.flush()


def _completed_run(session, instrument_id, closes, now, fast=2, slow=3) -> StrategyRun:
    """A real, completed strategy_run with real Signal rows -- Stage D's own
    machinery, reused as-is."""
    opens = session_bar_opens(DAY, CAL)
    run = run_strategy(
        session, user_id=uuid4(), instrument_ids=[instrument_id], range_start=opens[0],
        range_end=opens[len(closes) - 1] + timedelta(minutes=5), fast_period=fast,
        slow_period=slow, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), reset_ma_each_session=False, now=now, **COSTS,
    )
    assert run.status == "completed"
    return run


@run_db_tests
def test_happy_path_persists_orders_and_fills_and_sets_paper_status(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    _make_compliant(db_conn, inst.id, now)
    db_conn.commit()
    run = _completed_run(db_conn, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5], now)

    result = run_paper_engine(db_conn, run.id, now)

    assert result.paper_status == "completed"
    assert result.paper_completed_at == now
    orders = db_conn.scalars(
        sa.select(PaperOrder).where(PaperOrder.strategy_run_id == run.id)
        .order_by(PaperOrder.execution_ts)
    ).all()
    assert [o.side for o in orders if o.origin == "signal"] == ["buy", "sell"]
    fills = db_conn.scalars(
        sa.select(PaperFill).join(PaperOrder).where(PaperOrder.strategy_run_id == run.id)
    ).all()
    assert len(fills) == sum(1 for o in orders if o.status == "filled")


@run_db_tests
def test_second_call_raises_already_run(db_conn):
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    _make_compliant(db_conn, inst.id, now)
    db_conn.commit()
    run = _completed_run(db_conn, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5], now)

    run_paper_engine(db_conn, run.id, now)
    with pytest.raises(PaperEngineAlreadyRun):
        run_paper_engine(db_conn, run.id, now)


@run_db_tests
def test_already_run_guard_also_applies_to_zero_orders(db_conn):
    """A flat series with no crosses -> zero signals -> zero paper_orders. Even
    then, paper_status must flip to 'completed' and a second call must still
    refuse -- the guard is about re-processing the RUN, not about whether
    anything was produced."""
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 10, 10])
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    _make_compliant(db_conn, inst.id, now)
    db_conn.commit()
    run = _completed_run(db_conn, inst.id, [10, 10, 10, 10, 10, 10], now)

    result = run_paper_engine(db_conn, run.id, now)
    assert result.paper_status == "completed"
    order_count = db_conn.scalar(
        sa.select(sa.func.count()).select_from(PaperOrder).where(PaperOrder.strategy_run_id == run.id))
    assert order_count == 0

    with pytest.raises(PaperEngineAlreadyRun):
        run_paper_engine(db_conn, run.id, now)


@run_db_tests
def test_currency_mismatch_regression_guard(db_conn):
    """Unreachable through the normal flow (Stage D's run_strategy already
    refuses a mixed-currency run before any signal exists) -- this proves the
    defensive check inside run_paper_engine itself is real, by flipping an
    already-loaded Instrument's currency in memory (SQLAlchemy's identity map
    means run_paper_engine's own session.get() returns this same mutated
    object) rather than constructing an impossible end-to-end scenario.

    Stage H review finding: this used to be a plain `assert`, which `python -O`
    strips out entirely -- silently letting a currency-mismatched run through
    in an optimized deployment instead of failing it. Now an explicit raise, so
    this asserts the real exception type and its fields, not just "something
    raised"."""
    inst = _instrument(db_conn)
    _insert_bars(db_conn, inst.id, [10, 10, 10, 10, 20, 30])
    now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
    _make_compliant(db_conn, inst.id, now)
    db_conn.commit()
    run = _completed_run(db_conn, inst.id, [10, 10, 10, 10, 20, 30], now)

    inst.currency = "EUR"  # in-memory only, never flushed
    with pytest.raises(EngineCurrencyMismatch) as exc:
        run_paper_engine(db_conn, run.id, now)
    assert exc.value.run_id == run.id
    assert exc.value.instrument_id == inst.id
    assert exc.value.instrument_currency == "EUR"
    assert exc.value.run_currency == "USD"


@run_db_tests
def test_concurrent_calls_produce_exactly_one_set_of_rows(live_db_url, monkeypatch):
    """Real row-lock proof: two separate connections both call run_paper_engine
    for the SAME strategy_run_id at (as close to) the same time. SELECT ... FOR
    UPDATE must serialize them -- exactly one succeeds, the other raises
    PaperEngineAlreadyRun, and exactly one set of orders/fills ever exists."""
    setup_engine = sa.create_engine(live_db_url)
    run_id = None
    # Unique per run (not just "ZPAPERCONC"): this test commits for real on a
    # separate connection, outside the savepoint-rollback db_conn fixture, so a
    # fixed symbol would collide with the row a PREVIOUS run of this same test
    # left behind in db_test.
    symbol = f"ZPAPERCONC{uuid4().hex[:8].upper()}"
    try:
        with Session(setup_engine) as setup:
            inst = _instrument(setup, symbol=symbol)
            _insert_bars(setup, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5])
            now = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)
            # notes deliberately look like a demo fixture: compliance_checks is
            # append-only (the row can never be deleted), and db_test doubles as
            # the stand-in "target" database in test_demo_mode.py's non-demo-data
            # guard tests -- an un-tagged row here would permanently trip that
            # guard for the rest of this container's lifetime.
            _make_compliant(setup, inst.id, now, notes=(
                "DEMO FIXTURE, NOT A RULING -- not a real demo seed, a Stage E "
                "concurrency-test leftover kept demo-tagged because this table "
                "is append-only"))
            setup.commit()
            run = _completed_run(setup, inst.id, [10, 10, 10, 10, 20, 30, 5, 5, 5, 5], now)
            run_id = run.id

        # Slow the pure computation down so the two threads' calls genuinely
        # overlap inside the locked section instead of racing to be first.
        original = paper_engine.simulate_fills

        def slow_simulate_fills(*args, **kwargs):
            time.sleep(0.4)
            return original(*args, **kwargs)

        monkeypatch.setattr(paper_engine, "simulate_fills", slow_simulate_fills)

        outcomes: dict[str, str] = {}

        def call_it(key: str) -> None:
            engine = sa.create_engine(live_db_url)
            try:
                with Session(engine) as s:
                    try:
                        run_paper_engine(s, run_id, now)
                        outcomes[key] = "ok"
                    except PaperEngineAlreadyRun:
                        outcomes[key] = "already_run"
            finally:
                engine.dispose()

        t1 = threading.Thread(target=call_it, args=("a",))
        t2 = threading.Thread(target=call_it, args=("b",))
        t1.start()
        time.sleep(0.1)  # let t1 acquire the row lock first
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert sorted(outcomes.values()) == ["already_run", "ok"]

        with Session(setup_engine) as check:
            order_count = check.scalar(
                sa.select(sa.func.count()).select_from(PaperOrder)
                .where(PaperOrder.strategy_run_id == run_id))
            assert order_count == 2  # the buy and the sell -- not 4
    finally:
        if run_id is not None:
            with Session(setup_engine) as cleanup:
                cleanup.execute(sa.delete(PaperFill).where(PaperFill.order_id.in_(
                    sa.select(PaperOrder.id).where(PaperOrder.strategy_run_id == run_id))))
                cleanup.execute(sa.delete(PaperOrder).where(PaperOrder.strategy_run_id == run_id))
                cleanup.execute(sa.delete(Signal).where(Signal.strategy_run_id == run_id))
                cleanup.execute(sa.delete(StrategyRun).where(StrategyRun.id == run_id))
                cleanup.execute(sa.delete(PriceBar).where(
                    PriceBar.instrument_id == sa.select(Instrument.id)
                    .where(Instrument.symbol == symbol).scalar_subquery()))
                cleanup.commit()
        setup_engine.dispose()
