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

    def symbol_for(self, symbol: str, exchange: str) -> str:
        """Maps a canonical (Instrument.symbol, Instrument.exchange MIC code) pair to
        whatever ticker string this provider's API expects. The provider-specific
        result of this method must never be stored or passed around outside the
        adapter that produced it -- callers pass it straight into get_bars() and
        discard it. Default: identity (fine for providers with one global ticker
        namespace, e.g. the mock and most US-only providers)."""
        return symbol

    @abstractmethod
    def get_bars(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[Bar]:
        """Bars with start <= ts < end, ascending by ts, tz-aware UTC.
        Raises ProviderError on failure. Never returns invented data."""
