"""CLAUDE.md section 9 must-pass tests, as strict-xfail placeholders.
When the real implementation lands, replace the body and REMOVE the xfail marker.
strict=True means an accidentally-passing placeholder fails the suite."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.market_calendar import session_bar_opens
from app.models.compliance import ComplianceCheck
from app.models.instrument import Instrument
from app.providers.price.base import PriceProvider, ProviderError
from app.services.backfill import run_backfill
from app.services.compliance import ComplianceBlocked, assert_tradable
from app.services.strategy import Bar, generate_signals
from app.services.strategy_run import CurrencyMismatch, run_strategy


def placeholder(stage: str):
    return pytest.mark.xfail(strict=True, reason=f"not implemented until Stage {stage}",
                             raises=NotImplementedError)


@placeholder("E")
def test_1_math_and_loss_calculations_are_correct():
    """P/L maths incl. losses (not just gains), Decimal throughout."""
    raise NotImplementedError


@placeholder("E")
def test_2_buys_exceeding_available_cash_are_rejected():
    raise NotImplementedError


def _mock_session_returning(check: ComplianceCheck | None) -> MagicMock:
    session = MagicMock()
    session.scalar.return_value = check
    return session


def test_3_non_compliant_or_expired_symbols_are_rejected():
    """Missing, not_halal, doubtful, not_covered, or expired record -> order refused.
    This is the GATE-level test (app/services/compliance.py::assert_tradable); an
    engine-level integration test is added at Stage E once the paper engine actually
    calls this gate before placing an order (CLAUDE.md section 5 / Stage D-E note)."""
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ComplianceBlocked) as exc:
        assert_tradable(_mock_session_returning(None), 1, now)
    assert exc.value.reason == "no_record"

    for status in ("not_halal", "doubtful", "not_covered"):
        check = ComplianceCheck(status=status, checked_at=now - timedelta(days=1),
                                expires_at=now + timedelta(days=89))
        with pytest.raises(ComplianceBlocked) as exc:
            assert_tradable(_mock_session_returning(check), 1, now)
        assert exc.value.reason == status

    expired = ComplianceCheck(status="halal", checked_at=now - timedelta(days=100),
                              expires_at=now - timedelta(days=1))
    with pytest.raises(ComplianceBlocked) as exc:
        assert_tradable(_mock_session_returning(expired), 1, now)
    assert exc.value.reason == "expired"


def test_4_no_future_bars_used_next_bar_execution():
    """Signal on bar T executes at the OPEN of bar T+1 (never bar T's own close),
    and altering or deleting bars AFTER T never changes the signal at T. Full
    property coverage (prefix-stability + perturbation over a longer synthetic
    series) lives in tests/test_strategy_engine.py; this is the must-pass-level
    proof using the same hand-computed fixture as test_hand_computed_2_3_period_
    buy_sell_and_ties there."""
    day = date(2026, 12, 15)
    opens = session_bar_opens(day, "NYSE")
    closes = [10, 10, 10, 10, 20, 30]
    bars = [Bar(ts=t, close=Decimal(c)) for t, c in zip(opens, closes)]
    range_start, range_end = bars[0].ts, bars[-1].ts + timedelta(minutes=5)

    baseline = generate_signals(bars, fast_period=2, slow_period=3, calendar="NYSE",
                                range_start=range_start, range_end=range_end)
    assert len(baseline) == 1
    assert baseline[0].execution_ts == opens[5]  # next bar's OPEN, not bar 4's close

    mutated = bars[:5] + [Bar(ts=bars[5].ts, close=Decimal(999))]
    mutated_signals = generate_signals(mutated, fast_period=2, slow_period=3, calendar="NYSE",
                                       range_start=range_start, range_end=range_end)
    assert mutated_signals == baseline, "altering the bar AFTER the signal changed it"

    truncated = bars[:5]
    truncated_signals = generate_signals(
        truncated, fast_period=2, slow_period=3, calendar="NYSE",
        range_start=range_start, range_end=bars[4].ts + timedelta(minutes=5))
    assert truncated_signals == baseline, "deleting the bar AFTER the signal changed it"


@placeholder("E")
def test_5_no_fills_when_market_closed_or_holiday():
    raise NotImplementedError


class _FailingProvider(PriceProvider):
    name = "failing"

    def get_bars(self, symbol, interval, start, end):
        raise ProviderError("simulated upstream failure")


def test_6_api_failure_raises_never_invents_a_price():
    """Provider failure -> error. No forward-fill, interpolation or last-known price.
    The session is a bare mock with no data configured: if the backfill service ever
    touched it to read or write a bar after the provider failed, this would raise
    (MagicMock has no real DB rows to serve) or the assert_not_called()s below would
    catch it."""
    instrument = Instrument(symbol="X", exchange="XNAS", currency="USD", name="x")
    instrument.id = 1
    session = MagicMock()
    now = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)

    report = run_backfill(session, _FailingProvider(), [instrument], now)

    ir = report.instruments[0]
    assert ir.error == "simulated upstream failure"
    assert ir.inserted == 0
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    session.scalars.assert_not_called()


@placeholder("E")
def test_7_identical_strategy_run_gives_identical_results():
    """Same config + same bars -> byte-identical signals, orders AND FILLS. Stage D
    proved this for signals alone (determinism + replay tests in
    test_strategy_engine.py / test_strategy_run_service.py) -- stays xfail until
    Stage E adds fills to the same guarantee."""
    raise NotImplementedError


def test_8_run_rejected_if_instruments_not_all_in_run_currency():
    """Single-currency simulation: no FX conversion. Mixed currency -> run rejected."""
    usd_inst = SimpleNamespace(id=1, currency="USD", exchange="XNAS")
    cad_inst = SimpleNamespace(id=2, currency="CAD", exchange="XTSE")
    session = MagicMock()
    session.get.side_effect = lambda _model, instrument_id: {1: usd_inst, 2: cad_inst}[instrument_id]
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(CurrencyMismatch) as exc:
        run_strategy(
            session, user_id=uuid4(), instrument_ids=[1, 2],
            range_start=datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc),
            range_end=datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
            fast_period=5, slow_period=20, currency="USD", starting_cash=Decimal("1000"),
            max_position_value=Decimal("200"), commission_per_order=Decimal("1"),
            spread_bps=Decimal("2"), slippage_bps=Decimal("1"),
            reset_ma_each_session=False, now=now,
        )
    assert exc.value.instrument_id == 2
    assert exc.value.instrument_currency == "CAD"
    assert exc.value.run_currency == "USD"
    session.add.assert_not_called()  # rejected before any strategy_runs row is created
