"""Deterministic fixture provider for tests. Data is synthetic and carries no market
meaning. Bars follow a triangle wave so MA crossovers occur predictably."""
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.providers.price.base import Bar, PriceProvider, ProviderError

BARS_PER_DAY = 78  # 6.5h of 5-minute bars
SESSION_OPEN_UTC = time(14, 30)  # fixture only; real code must use the market calendar
_ANCHOR = date(2026, 1, 1)
_CENT = Decimal("0.01")
_Q = Decimal("0.0001")


class MockProvider(PriceProvider):
    name = "mock"

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    @staticmethod
    def _base_cents(symbol: str) -> int:
        return 5000 + hashlib.sha256(symbol.encode()).digest()[0] * 10

    @staticmethod
    def _price_cents(base: int, n: int) -> int:
        return base + abs((n % 40) - 20) * 10  # triangle wave, period 40 bars

    def get_bars(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[Bar]:
        if self._fail:
            raise ProviderError("mock provider configured to fail")
        if interval != "5m":
            raise ProviderError(f"mock provider supports only 5m, got {interval!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ProviderError("start/end must be timezone-aware")

        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        base = self._base_cents(symbol)
        bars: list[Bar] = []
        day = start.date()
        while day <= end.date():
            if day.weekday() < 5:
                day_index = (day - _ANCHOR).days
                open_dt = datetime.combine(day, SESSION_OPEN_UTC, tzinfo=timezone.utc)
                for i in range(BARS_PER_DAY):
                    ts = open_dt + timedelta(minutes=5 * i)
                    if not (start <= ts < end):
                        continue
                    n = day_index * BARS_PER_DAY + i
                    o = self._price_cents(base, n - 1)
                    c = self._price_cents(base, n)
                    bars.append(Bar(
                        ts=ts,
                        open=(Decimal(o) * _CENT).quantize(_Q),
                        high=(Decimal(max(o, c) + 5) * _CENT).quantize(_Q),
                        low=(Decimal(min(o, c) - 5) * _CENT).quantize(_Q),
                        close=(Decimal(c) * _CENT).quantize(_Q),
                        volume=1000 + (n * 13) % 500,
                    ))
            day += timedelta(days=1)
        return bars
