"""Builds the ONLY data Stage G's template/LLM ever sees -- fully numeric (or
fixed-enum), never free text, never an instrument identity. This module is the
one and only place that decides what's safe to forward; it must never import
Instrument or ComplianceCheck at all (see tests/test_ai_summary_input.py's
static-scan test).

Explicitly EXCLUDED, by construction, not by filtering: instrument_ids/symbols/
names, blocked_instruments, anything from compliance_checks (status, notes,
evidence_url), config_hash, the raw equity_curve points, and
unresolved_instrument_ids (only the unresolved COUNT is passed through).
"""
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.paper import REJECTION_REASONS
from app.models.strategy import StrategyRun
from app.services.metrics import MetricsResult

_REJECTION_PLACEHOLDER_PREFIX = "rejection_"
_REJECTION_PLACEHOLDER_SUFFIX = "_count"


def _rejection_placeholder(reason: str) -> str:
    return f"{_REJECTION_PLACEHOLDER_PREFIX}{reason}{_REJECTION_PLACEHOLDER_SUFFIX}"


# The complete, fixed set of placeholder names a template or LLM output may
# use. Anything else in a `{...}` token is rejected (app/services/
# ai_summary_guards.py) before the text is ever trusted.
ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "trades_count", "realized_pnl", "total_return", "win_rate", "avg_win",
        "avg_non_win", "total_fees", "max_drawdown", "unresolved_positions",
        "fast_period", "slow_period", "reset_ma_each_session", "range_start",
        "range_end", "currency", "starting_cash", "max_position_value",
        "commission_per_order", "spread_bps", "slippage_bps",
    }
    | {_rejection_placeholder(r) for r in REJECTION_REASONS}
)

_NO_TRADES = "no trades"


def _pct(value: Decimal | None) -> str:
    return _NO_TRADES if value is None else f"{value * 100:.2f}%"


def _money(value: Decimal | None) -> str:
    return _NO_TRADES if value is None else f"${value}"


def compute_values_hash(values: dict[str, str]) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AiSummaryInput:
    """Every value here is already a plain, pre-formatted string or the
    underlying value used only to compute it -- the template/LLM substitution
    step (app/services/ai_summary.py) consumes `values` directly via
    str.format(**values); nothing else is ever interpolated into output text."""

    values: dict[str, str] = field(default_factory=dict)

    def input_hash(self) -> str:
        return compute_values_hash(self.values)


def build_ai_summary_input(
    run: StrategyRun, metrics: MetricsResult, rejection_counts: dict[str, int],
) -> AiSummaryInput:
    values: dict[str, str] = {
        "trades_count": str(metrics.trades_count),
        "realized_pnl": _money(metrics.realized_pnl),
        "total_return": _pct(metrics.total_return),
        "win_rate": _pct(metrics.win_rate),
        "avg_win": _money(metrics.avg_win),
        "avg_non_win": _money(metrics.avg_non_win),
        "total_fees": _money(metrics.total_fees),
        "max_drawdown": _pct(metrics.max_drawdown),
        "unresolved_positions": str(metrics.unresolved_positions),
        "fast_period": str(run.fast_period),
        "slow_period": str(run.slow_period),
        "reset_ma_each_session": "on" if run.reset_ma_each_session else "off",
        "range_start": run.range_start.date().isoformat(),
        "range_end": run.range_end.date().isoformat(),
        "currency": run.currency,
        "starting_cash": _money(run.starting_cash),
        "max_position_value": _money(run.max_position_value),
        "commission_per_order": _money(run.commission_per_order),
        "spread_bps": str(run.spread_bps),
        "slippage_bps": str(run.slippage_bps),
    }
    for reason in REJECTION_REASONS:
        values[_rejection_placeholder(reason)] = str(rejection_counts.get(reason, 0))

    assert set(values.keys()) == set(ALLOWED_PLACEHOLDERS), "values must match ALLOWED_PLACEHOLDERS exactly"
    return AiSummaryInput(values=values)
