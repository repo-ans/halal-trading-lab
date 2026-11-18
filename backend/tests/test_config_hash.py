from datetime import datetime, timezone
from decimal import Decimal

import app.services.config_hash as config_hash_module
from app.services.config_hash import build_config_hash


def _params(**overrides) -> dict:
    base = dict(
        instrument_ids=[2, 1, 3], range_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        range_end=datetime(2026, 2, 1, tzinfo=timezone.utc), fast_period=5, slow_period=20,
        reset_ma_each_session=False, currency="USD", starting_cash=Decimal("1000"),
        max_position_value=Decimal("200"), commission_per_order=Decimal("1"),
        spread_bps=Decimal("2"), slippage_bps=Decimal("1"), interval="5m",
    )
    base.update(overrides)
    return base


def test_same_params_give_same_hash():
    assert build_config_hash(**_params()) == build_config_hash(**_params())


def test_instrument_id_order_does_not_affect_hash():
    a = build_config_hash(**_params(instrument_ids=[1, 2, 3]))
    b = build_config_hash(**_params(instrument_ids=[3, 1, 2]))
    assert a == b


def test_changing_any_signal_relevant_field_changes_the_hash():
    baseline = build_config_hash(**_params())
    changes = [
        {"fast_period": 6}, {"slow_period": 21}, {"reset_ma_each_session": True},
        {"currency": "CAD"}, {"starting_cash": Decimal("2000")},
        {"max_position_value": Decimal("300")}, {"commission_per_order": Decimal("2")},
        {"spread_bps": Decimal("3")}, {"slippage_bps": Decimal("2")},
        {"instrument_ids": [1, 2, 3, 4]}, {"interval": "1m"},
        {"range_start": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"range_end": datetime(2026, 2, 2, tzinfo=timezone.utc)},
    ]
    for change in changes:
        assert build_config_hash(**_params(**change)) != baseline, f"no change from {change}"


def test_money_fields_hash_the_same_regardless_of_decimal_scale():
    """A literal Decimal("1000") and the same value read back from a NUMERIC(18,4)
    column (Decimal("1000.0000"), as replay_strategy_run does from the stored
    strategy_runs row) must hash identically -- caught by the replay round-trip
    test actually hitting this before this normalization was added."""
    a = build_config_hash(**_params(starting_cash=Decimal("1000")))
    b = build_config_hash(**_params(starting_cash=Decimal("1000.0000")))
    assert a == b


def test_hash_includes_strategy_rules_version(monkeypatch):
    before = build_config_hash(**_params())
    monkeypatch.setattr(config_hash_module, "STRATEGY_RULES_VERSION", "ma-cross-v2")
    after = build_config_hash(**_params())
    assert before != after


def test_bookkeeping_fields_are_not_part_of_the_hash():
    """started_at/finished_at/run id are never arguments to build_config_hash at
    all -- this just documents that fact so a future change doesn't quietly add
    one without someone noticing the determinism property it would break."""
    import inspect
    sig = inspect.signature(build_config_hash)
    assert "started_at" not in sig.parameters
    assert "finished_at" not in sig.parameters
