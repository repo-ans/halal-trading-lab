import pathlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.strategy import StrategyRun
from app.services.ai_summary_input import ALLOWED_PLACEHOLDERS, build_ai_summary_input
from app.services.metrics import MetricsResult


def test_module_never_imports_instrument_or_compliance():
    """The one and only place that decides what's safe to forward to a
    template/LLM must never have access to instrument identity or compliance
    free text at all -- not filtered out at runtime, structurally absent.
    Checks actual import lines, not the module's own docstring explaining
    why (which necessarily names both to say they're excluded)."""
    source = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "app", "services", "ai_summary_input.py").read_text(encoding="utf-8")
    import_lines = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    offenders = [line for line in import_lines if "instrument" in line.lower() or "compliance" in line.lower()]
    assert offenders == []


def _run() -> StrategyRun:
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    return StrategyRun(
        user_id=uuid4(), currency="USD", instrument_ids=[1],
        range_start=now - timedelta(days=1), range_end=now,
        fast_period=5, slow_period=20, reset_ma_each_session=False,
        starting_cash=Decimal("1000"), max_position_value=Decimal("200"),
        commission_per_order=Decimal("1.00"), spread_bps=Decimal("2"),
        slippage_bps=Decimal("1"), config_hash="x" * 64, status="completed",
    )


def _metrics(**overrides) -> MetricsResult:
    base = dict(
        trades_count=0, realized_pnl=None, total_return=None, win_rate=None,
        avg_win=None, avg_non_win=None, total_fees=Decimal("0"), max_drawdown=Decimal("0"),
        unresolved_positions=0, unresolved_instrument_ids=[], equity_curve=[],
    )
    base.update(overrides)
    return MetricsResult(**base)


def test_values_exactly_match_allowed_placeholders():
    ai_input = build_ai_summary_input(_run(), _metrics(), {})
    assert set(ai_input.values.keys()) == ALLOWED_PLACEHOLDERS


def test_null_metrics_render_as_no_trades():
    ai_input = build_ai_summary_input(_run(), _metrics(), {})
    assert ai_input.values["realized_pnl"] == "no trades"
    assert ai_input.values["win_rate"] == "no trades"


def test_real_metrics_render_with_dollar_and_percent_signs():
    ai_input = build_ai_summary_input(
        _run(), _metrics(trades_count=2, realized_pnl=Decimal("-4.0000"),
                         total_return=Decimal("-0.0040"), win_rate=Decimal("0.5000")), {},
    )
    assert ai_input.values["realized_pnl"] == "$-4.0000"
    assert ai_input.values["total_return"] == "-0.40%"
    assert ai_input.values["trades_count"] == "2"


def test_rejection_counts_fill_all_seven_placeholders():
    ai_input = build_ai_summary_input(_run(), _metrics(), {"insufficient_cash": 3})
    assert ai_input.values["rejection_insufficient_cash_count"] == "3"
    assert ai_input.values["rejection_execution_bar_missing_count"] == "0"


def test_input_hash_is_deterministic_and_sensitive_to_changes():
    a = build_ai_summary_input(_run(), _metrics(), {})
    b = build_ai_summary_input(_run(), _metrics(), {})
    assert a.input_hash() == b.input_hash()

    c = build_ai_summary_input(_run(), _metrics(trades_count=1), {})
    assert c.input_hash() != a.input_hash()
