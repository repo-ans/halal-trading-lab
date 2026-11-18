"""DEV ONLY. CLAUDE.md section 3: the yfinance/Yahoo API is not licensed for
production use -- this adapter refuses to construct unless ALLOW_DEV_DATA_SOURCE is
explicitly true. Every bar it returns is written downstream with source='yfinance'
(see app.services.backfill), so Stage H can find and purge every row it ever wrote
before any non-owner user logs in (CLAUDE.md section 3 checklist).

yfinance is imported lazily (inside the methods, not at module scope) so importing
this module never requires the dependency to be usable -- only constructing the
provider with ALLOW_DEV_DATA_SOURCE=true does."""
from datetime import datetime, timezone
from decimal import Decimal

from app.config import get_settings
from app.providers.price.base import Bar, PriceProvider, ProviderError

# Money columns are NUMERIC(18,4) (CLAUDE.md section 8) -- quantize here, not just at
# INSERT time. yfinance's underlying floats carry float32-sized noise past 4 decimal
# places (e.g. 334.5411071777344 for what is really 334.5411); comparing an unquantized
# fresh value against a DB-rounded stored value made every single refetched bar look
# "different" and spuriously fire the differs-but-kept warning on every bar, every run.
_PRICE_Q = Decimal("0.0001")

# MIC code -> yfinance ticker suffix. Only the exchanges app/seed/instruments.json
# actually uses; add more here (never elsewhere) as new exchanges are seeded.
_EXCHANGE_SUFFIX = {
    "XNAS": "",
    "XNYS": "",
    "XTSE": ".TO",
}

_REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class YFinanceProvider(PriceProvider):
    name = "yfinance"

    def __init__(self) -> None:
        if not get_settings().allow_dev_data_source:
            raise ProviderError(
                "YFinanceProvider is dev-only (CLAUDE.md section 3: yfinance/Yahoo is "
                "not licensed for production use). Set ALLOW_DEV_DATA_SOURCE=true to "
                "use it in development.")

    def symbol_for(self, symbol: str, exchange: str) -> str:
        try:
            suffix = _EXCHANGE_SUFFIX[exchange]
        except KeyError:
            raise ProviderError(
                f"yfinance: no ticker mapping for exchange {exchange!r}") from None
        return f"{symbol}{suffix}"

    def get_bars(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[Bar]:
        """symbol must already be the yfinance-native ticker (see symbol_for) --
        callers must not pass a canonical Instrument.symbol directly."""
        if interval != "5m":
            raise ProviderError(f"yfinance provider supports only 5m, got {interval!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ProviderError("start/end must be timezone-aware")

        import yfinance as yf

        try:
            df = yf.Ticker(symbol).history(
                interval="5m", start=start, end=end,
                prepost=False,  # regular session only
                auto_adjust=False,  # raw traded prices, not split/dividend-adjusted
                actions=False,
                raise_errors=True,
                # Explicit, not just relying on yfinance's own default: a "repair" that
                # silently patches outlier/zero prices is exactly the kind of invented
                # data CLAUDE.md section 9 must-pass test 6 forbids.
                repair=False,
            )
        except ProviderError:
            raise
        except Exception as exc:  # yfinance raises a mix of its own + requests errors
            raise ProviderError(f"yfinance request failed for {symbol!r}: {exc}") from exc

        if df.empty:
            raise ProviderError(f"yfinance returned no bars for {symbol!r} in "
                                f"[{start.isoformat()}, {end.isoformat()})")

        missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ProviderError(
                f"yfinance response missing columns {missing} for {symbol!r}")
        if df[list(_REQUIRED_COLUMNS)].isna().any().any():
            raise ProviderError(
                f"yfinance returned a partial response (NaNs) for {symbol!r}")
        if df.index.tz is None:
            raise ProviderError(
                f"yfinance returned timezone-naive timestamps for {symbol!r}")

        df = df.tz_convert(timezone.utc)
        bars = []
        for ts, row in df.iterrows():
            bars.append(Bar(
                ts=ts.to_pydatetime(),
                open=Decimal(str(float(row["Open"]))).quantize(_PRICE_Q),
                high=Decimal(str(float(row["High"]))).quantize(_PRICE_Q),
                low=Decimal(str(float(row["Low"]))).quantize(_PRICE_Q),
                close=Decimal(str(float(row["Close"]))).quantize(_PRICE_Q),
                volume=int(row["Volume"]),
            ))
        return bars
