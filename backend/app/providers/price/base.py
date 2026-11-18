"""Price provider interface. No provider-specific types, fields or symbol formats may
leak past an adapter: adapters take/return only the types defined here."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class ProviderError(Exception):
    """Raised on any provider failure. Callers must fail loudly -- never forward-fill,
    interpolate, or substitute a last-known price (CLAUDE.md section 9, test 6)."""


@dataclass(frozen=True)
class Bar:
    ts: datetime  # bar OPEN time, tz-aware UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class PriceProvider(ABC):
    name: str

    @abstractmethod
    def get_bars(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[Bar]:
        """Bars with start <= ts < end, ascending by ts, tz-aware UTC.
        Raises ProviderError on failure. Never returns invented data."""
