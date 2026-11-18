"""Compliance provider interface. The app NEVER computes or infers a halal ruling;
providers only relay a human/third-party status with evidence."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

STATUSES = ("halal", "not_halal", "doubtful", "not_covered")
SOURCES = ("manual", "zoya", "musaffa")


@dataclass(frozen=True)
class ComplianceRecord:
    symbol: str
    status: str  # one of STATUSES
    source: str  # one of SOURCES
    checked_at: datetime  # tz-aware UTC
    expires_at: datetime  # tz-aware UTC
    evidence_url: str | None = None


class ComplianceProvider(ABC):
    name: str

    @abstractmethod
    def get_record(self, symbol: str) -> ComplianceRecord | None:
        """Current record for symbol, or None if there is none.
        A missing record must be treated by callers as NOT compliant."""
