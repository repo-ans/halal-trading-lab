"""Fixture provider for tests. MOCK* symbols are fake and are NOT halal rulings."""
from datetime import datetime, timezone

from app.providers.compliance.base import ComplianceProvider, ComplianceRecord

_T = lambda y, m, d: datetime(y, m, d, tzinfo=timezone.utc)  # noqa: E731

_FIXTURES = {
    "MOCKA": ComplianceRecord("MOCKA", "halal", "manual", _T(2026, 11, 1), _T(2027, 1, 30)),
    "MOCKB": ComplianceRecord("MOCKB", "not_halal", "manual", _T(2026, 11, 1), _T(2027, 1, 30)),
    "MOCKC": ComplianceRecord("MOCKC", "doubtful", "manual", _T(2026, 11, 1), _T(2027, 1, 30)),
    "MOCKD": ComplianceRecord("MOCKD", "halal", "manual", _T(2026, 1, 1), _T(2026, 4, 1)),  # expired
}


class MockComplianceProvider(ComplianceProvider):
    name = "mock"

    def get_record(self, symbol: str) -> ComplianceRecord | None:
        return _FIXTURES.get(symbol)
