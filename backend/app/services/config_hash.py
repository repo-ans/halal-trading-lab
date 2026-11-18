"""Canonical hash of a strategy run's config, so rerunning the identical config
gives the identical hash (CLAUDE.md section 7's determinism must-pass test) and a
different config is guaranteed to hash differently. Only the inputs that determine
the SIGNALS a run produces go in here -- never started_at/finished_at/run id, which
are bookkeeping and would make the hash non-deterministic across reruns by design."""
import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.services.strategy import STRATEGY_RULES_VERSION

_MONEY_QUANT = Decimal("0.0001")  # matches NUMERIC(18,4) -- see app/models/base.py's Money type


def _normalize_money(value: Decimal) -> Decimal:
    """A freshly-constructed Decimal("1000") and the SAME value read back from a
    NUMERIC(18,4) column (Decimal("1000.0000")) must hash identically -- replay
    reads its config straight off the stored strategy_runs row, so without this
    normalization its config_hash would differ from the original run's purely from
    string formatting, never an actual config difference."""
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON-hashable: {value!r}")


def compute_config_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, default=_json_default, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_config_hash(
    *,
    instrument_ids: list[int],
    range_start: datetime,
    range_end: datetime,
    fast_period: int,
    slow_period: int,
    reset_ma_each_session: bool,
    currency: str,
    starting_cash: Decimal,
    max_position_value: Decimal,
    commission_per_order: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
    interval: str,
) -> str:
    params = {
        "strategy_rules_version": STRATEGY_RULES_VERSION,
        "instrument_ids": sorted(instrument_ids),
        "range_start": range_start,
        "range_end": range_end,
        "fast_period": fast_period,
        "slow_period": slow_period,
        "reset_ma_each_session": reset_ma_each_session,
        "interval": interval,
        "currency": currency,
        "starting_cash": _normalize_money(starting_cash),
        "max_position_value": _normalize_money(max_position_value),
        "commission_per_order": _normalize_money(commission_per_order),
        "spread_bps": _normalize_money(spread_bps),
        "slippage_bps": _normalize_money(slippage_bps),
    }
    return compute_config_hash(params)
