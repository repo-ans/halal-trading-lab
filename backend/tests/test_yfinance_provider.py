"""All fixture-based -- yfinance.Ticker is monkeypatched, nothing here touches the
network (CLAUDE.md section 9 must-pass test 6 lives in test_must_pass.py and reuses
this same fake-ticker approach)."""
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from app.config import get_settings
from app.providers.price.base import ProviderError
from app.providers.price.yfinance_provider import YFinanceProvider

START = datetime(2026, 11, 16, tzinfo=timezone.utc)
END = datetime(2026, 11, 17, tzinfo=timezone.utc)


@pytest.fixture
def allow_dev(monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_dev_data_source", True)


def _make_df(rows: list[tuple]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([r[0] for r in rows], tz="America/New_York")
    return pd.DataFrame({
        "Open": [r[1] for r in rows],
        "High": [r[2] for r in rows],
        "Low": [r[3] for r in rows],
        "Close": [r[4] for r in rows],
        "Volume": [r[5] for r in rows],
    }, index=idx)


def _install_fake_ticker(monkeypatch, result, captured_kwargs: dict | None = None):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            if captured_kwargs is not None:
                captured_kwargs.update(kwargs)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)


def test_raises_unless_allow_dev_data_source(monkeypatch):
    # Explicit, not relying on the ambient .env default -- ALLOW_DEV_DATA_SOURCE may
    # legitimately be true in a developer's own .env (e.g. while running the real
    # collector), and this test must still prove the guard exists regardless.
    monkeypatch.setattr(get_settings(), "allow_dev_data_source", False)
    with pytest.raises(ProviderError):
        YFinanceProvider()


def test_symbol_for_us_exchanges_no_suffix(allow_dev):
    p = YFinanceProvider()
    assert p.symbol_for("AAPL", "XNAS") == "AAPL"
    assert p.symbol_for("KO", "XNYS") == "KO"


def test_symbol_for_tsx_adds_suffix(allow_dev):
    assert YFinanceProvider().symbol_for("RY", "XTSE") == "RY.TO"


def test_symbol_for_unknown_exchange_raises(allow_dev):
    with pytest.raises(ProviderError):
        YFinanceProvider().symbol_for("XXX", "ZZZZ")


def test_rejects_unsupported_interval(allow_dev):
    with pytest.raises(ProviderError):
        YFinanceProvider().get_bars("AAPL", "1d", START, END)


def test_rejects_naive_start_end(allow_dev):
    with pytest.raises(ProviderError):
        YFinanceProvider().get_bars("AAPL", "5m", START.replace(tzinfo=None), END)


def test_requests_unadjusted_unrepaired_regular_session_data(allow_dev, monkeypatch):
    """Proves the actual kwargs sent to yfinance, not just that the code reads
    auto_adjust=False in a comment. repair=True or auto_adjust=True would silently
    substitute yfinance's own guesses for real data, which is exactly the kind of
    invented price CLAUDE.md section 9 must-pass test 6 forbids."""
    captured: dict = {}
    _install_fake_ticker(monkeypatch, _make_df([
        (datetime(2026, 11, 16, 9, 30), 150.0, 151.0, 149.5, 150.5, 10000),
    ]), captured_kwargs=captured)

    YFinanceProvider().get_bars("AAPL", "5m", START, END)

    assert captured["auto_adjust"] is False
    assert captured["repair"] is False
    assert captured["prepost"] is False


def test_returns_exact_expected_timestamp_set(allow_dev, monkeypatch):
    """Not just a count -- the exact set of UTC timestamps a known 3-bar fixture
    should produce, each exactly 5 minutes apart starting at the fixture's first
    ET timestamp converted to UTC."""
    rows = [
        (datetime(2026, 11, 16, 9, 30), 150.0, 150.5, 149.8, 150.2, 1000),
        (datetime(2026, 11, 16, 9, 35), 150.2, 150.9, 150.0, 150.7, 1100),
        (datetime(2026, 11, 16, 9, 40), 150.7, 151.0, 150.3, 150.4, 1200),
    ]
    _install_fake_ticker(monkeypatch, _make_df(rows))

    bars = YFinanceProvider().get_bars("AAPL", "5m", START, END)

    expected = {
        datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 14, 35, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 14, 40, tzinfo=timezone.utc),
    }
    assert {b.ts for b in bars} == expected


def test_converts_to_utc_and_decimal(allow_dev, monkeypatch):
    # 09:30 ET on 2026-11-16 is EST (winter) -- 14:30 UTC, matching the mock fixture's
    # winter-only constant (see test_market_calendar.py).
    _install_fake_ticker(monkeypatch, _make_df([
        (datetime(2026, 11, 16, 9, 30), 150.0, 151.25, 149.5, 150.75, 10000),
    ]))
    bars = YFinanceProvider().get_bars("AAPL", "5m", START, END)
    assert len(bars) == 1
    b = bars[0]
    assert b.ts == datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)
    assert b.ts.tzinfo is not None
    for v in (b.open, b.high, b.low, b.close):
        assert isinstance(v, Decimal)
    assert b.open == Decimal("150.0")
    assert b.close == Decimal("150.75")
    assert b.volume == 10000


def test_quantizes_prices_to_four_decimal_places(allow_dev, monkeypatch):
    """Regression test: yfinance's underlying float32-ish values carry noise past 4
    decimal places (334.5411071777344 for what a NUMERIC(18,4) column stores as
    334.5411 -- this exact value was observed live against production). Without
    quantizing here, comparing a freshly-fetched bar against the DB-rounded stored
    value made every single refetched bar look "different", firing the differs-but-
    kept warning on every bar of every rerun."""
    _install_fake_ticker(monkeypatch, _make_df([
        (datetime(2026, 11, 16, 9, 30), 334.5411071777344, 335.0, 334.0, 334.5411071777344, 1000),
    ]))
    bars = YFinanceProvider().get_bars("AAPL", "5m", START, END)
    assert bars[0].open == Decimal("334.5411")
    assert bars[0].close == Decimal("334.5411")
    assert str(bars[0].close) == "334.5411"  # exactly 4 places, not just numerically equal


def test_empty_response_raises(allow_dev, monkeypatch):
    _install_fake_ticker(monkeypatch, pd.DataFrame())
    with pytest.raises(ProviderError):
        YFinanceProvider().get_bars("AAPL", "5m", START, END)


def test_partial_response_with_nan_raises(allow_dev, monkeypatch):
    df = _make_df([(datetime(2026, 11, 16, 9, 30), 150.0, 151.0, 149.5, 150.5, 10000)])
    df.loc[df.index[0], "Close"] = float("nan")
    _install_fake_ticker(monkeypatch, df)
    with pytest.raises(ProviderError):
        YFinanceProvider().get_bars("AAPL", "5m", START, END)


def test_request_failure_raises_provider_error(allow_dev, monkeypatch):
    _install_fake_ticker(monkeypatch, RuntimeError("connection reset"))
    with pytest.raises(ProviderError):
        YFinanceProvider().get_bars("AAPL", "5m", START, END)


def test_timezone_naive_index_raises(allow_dev, monkeypatch):
    idx = pd.DatetimeIndex([datetime(2026, 11, 16, 9, 30)])  # no tz -- malformed response
    df = pd.DataFrame({"Open": [150.0], "High": [151.0], "Low": [149.5],
                       "Close": [150.5], "Volume": [10000]}, index=idx)
    _install_fake_ticker(monkeypatch, df)
    with pytest.raises(ProviderError):
        YFinanceProvider().get_bars("AAPL", "5m", START, END)
