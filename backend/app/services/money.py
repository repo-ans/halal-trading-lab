"""The one named rounding rule, used on every money value right before it is
stored or applied to cash (CLAUDE.md section 7 / the Stage E plan's point 1).
Never used mid-calculation -- rounding early would compound error across a run."""
from decimal import ROUND_HALF_UP, Decimal

MONEY_QUANT = Decimal("0.0001")  # matches NUMERIC(18,4), app/models/base.py's Money type


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
