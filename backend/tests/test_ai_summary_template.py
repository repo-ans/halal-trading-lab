"""Pure: no DB, no clock. render_raw_template() is a fixed string -- these
tests prove it stays that way, and that it would survive its own guards."""
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from app.models.strategy import StrategyRun
from app.services.ai_summary_guards import validate_raw_generated_text
from app.services.ai_summary_input import build_ai_summary_input
from app.services.ai_summary_template import render_raw_template
from app.services.metrics import MetricsResult

# Same fixture numbers as the real DEMO-OK run verified live in the browser
# during Stage F (realized P&L -$14.2876, 4 trades, 0% win rate) -- chosen
# because it's already a known-correct, independently-verified data point,
# not just an arbitrary fixture.
_NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
_PINNED_HASH = "07c314d8767263255670b745a3583204a5922312ecbf8178f2e5d53f6837549c"


def _run() -> StrategyRun:
    return StrategyRun(
        user_id=UUID("00000000-0000-0000-0000-000000000000"), currency="USD", instrument_ids=[1],
        range_start=_NOW - timedelta(days=2), range_end=_NOW,
        fast_period=5, slow_period=20, reset_ma_each_session=False,
        starting_cash=Decimal("1000"), max_position_value=Decimal("200"),
        commission_per_order=Decimal("1.00"), spread_bps=Decimal("2"),
        slippage_bps=Decimal("1"), config_hash="x" * 64, status="completed",
    )


def _metrics() -> MetricsResult:
    return MetricsResult(
        trades_count=4, realized_pnl=Decimal("-14.2876"), total_return=Decimal("-0.0143"),
        win_rate=Decimal("0.0000"), avg_win=None, avg_non_win=Decimal("-3.5719"),
        total_fees=Decimal("9.1276"), max_drawdown=Decimal("0.0143"), unresolved_positions=0,
        unresolved_instrument_ids=[], equity_curve=[],
    )


def _rendered_text() -> str:
    ai_input = build_ai_summary_input(_run(), _metrics(), {"no_open_position_to_sell": 1})
    return render_raw_template().format(**ai_input.values)


def test_raw_template_passes_its_own_output_guards():
    assert validate_raw_generated_text(render_raw_template()) is None


def test_rendering_is_deterministic():
    assert _rendered_text() == _rendered_text()


def test_pinned_output_hash():
    """Changing RAW_TEMPLATE_TEXT's wording breaks this test ON PURPOSE -- if
    that's what you meant to do, bump PROMPT_VERSION in
    app/services/ai_summary_template.py AND update _PINNED_HASH above
    deliberately, in the same change, not as an afterthought."""
    text = _rendered_text()
    actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert actual_hash == _PINNED_HASH, (
        f"template output changed (new hash: {actual_hash}) -- bump PROMPT_VERSION "
        "and update _PINNED_HASH deliberately if this wording change was intended"
    )
