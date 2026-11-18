from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.providers.compliance import MockComplianceProvider
from app.providers.price import MockProvider, ProviderError

START = datetime(2026, 11, 16, tzinfo=timezone.utc)  # Monday
END = datetime(2026, 11, 18, tzinfo=timezone.utc)


def test_deterministic():
    p = MockProvider()
    assert p.get_bars("MOCKA", "5m", START, END) == MockProvider().get_bars("MOCKA", "5m", START, END)


def test_two_trading_days_of_78_bars():
    assert len(MockProvider().get_bars("MOCKA", "5m", START, END)) == 156


def test_bars_are_decimal_utc_sorted_and_sane():
    bars = MockProvider().get_bars("MOCKA", "5m", START, END)
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)
    for b in bars:
        assert b.ts.tzinfo is not None and b.ts.utcoffset().total_seconds() == 0
        for v in (b.open, b.high, b.low, b.close):
            assert isinstance(v, Decimal)
        assert b.high >= max(b.open, b.close) and b.low <= min(b.open, b.close)


def test_overlapping_ranges_agree():
    p = MockProvider()
    full = p.get_bars("MOCKA", "5m", START, END)
    tail = p.get_bars("MOCKA", "5m", datetime(2026, 11, 17, tzinfo=timezone.utc), END)
    assert full[-len(tail):] == tail


def test_weekend_has_no_bars():
    sat = datetime(2026, 11, 21, tzinfo=timezone.utc)
    assert MockProvider().get_bars("MOCKA", "5m", sat, datetime(2026, 11, 23, tzinfo=timezone.utc)) == []


def test_failure_raises():
    with pytest.raises(ProviderError):
        MockProvider(fail=True).get_bars("MOCKA", "5m", START, END)


def test_compliance_fixtures():
    c = MockComplianceProvider()
    assert c.get_record("MOCKA").status == "halal"
    assert c.get_record("NOPE") is None
