"""Shared adverse-price-adjustment / cost-breakdown formula. Used identically by
the automated paper engine (Stage E, app/services/paper_engine.py) and the
interactive replay engine (Replay Trading, app/services/replay_engine.py) --
CLAUDE.md section 7: a simulation without costs reports profits that cannot
exist in reality, and both engines must apply exactly the same spread/
slippage/commission model, never two hand-copied versions that could drift.
Extracted out of paper_engine.py when Replay Trading was built (2026-11-23);
paper_engine.py's own `_adverse_fill_price`/`_cost_breakdown` are now thin
aliases onto this module so its existing imports/tests keep working unchanged.
"""
from decimal import Decimal

from app.services.money import quantize_money

_BPS = Decimal(10000)


def adverse_fill_price(raw_open: Decimal, spread_bps: Decimal, slippage_bps: Decimal, side: str) -> Decimal:
    adjustment = spread_bps / 2 + slippage_bps
    if side == "buy":
        return quantize_money(raw_open * (1 + adjustment / _BPS))
    return quantize_money(raw_open * (1 - adjustment / _BPS))


def cost_breakdown(raw_open: Decimal, spread_bps: Decimal, slippage_bps: Decimal,
                   qty: Decimal) -> tuple[Decimal, Decimal]:
    """spread_cost, slippage_cost -- reporting-only breakdowns, independently
    rounded. Never added to a cash check or cash movement a second time (that's
    entirely driven by fill_price, computed separately)."""
    spread_cost = quantize_money(raw_open * (spread_bps / 2 / _BPS) * qty)
    slippage_cost = quantize_money(raw_open * (slippage_bps / _BPS) * qty)
    return spread_cost, slippage_cost
