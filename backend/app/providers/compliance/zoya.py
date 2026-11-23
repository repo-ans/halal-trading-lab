"""Zoya Finance's GraphQL API (CLAUDE.md section 4's "later" B2B provider --
sandbox access obtained 2026-11-23, no live key yet).

LOOKUP ONLY. This provider is never wired into any write path. CLAUDE.md
section 5's rule ("a human verifies the evidence before anything is written")
still applies to Zoya-sourced data -- Zoya's basicCompliance.report has no
evidence URL at all, and the sandbox endpoint returns randomized dummy data
per Zoya's own developer dashboard, so neither is fit to write to
compliance_checks unattended. See app/routes/compliance.py's
GET /compliance/zoya-lookup: it calls get_record() and returns a SUGGESTION
for an admin to review, add real evidence to, and submit through the
existing, already-tested POST /compliance path -- this module has no
knowledge of compliance_checks at all.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.providers.compliance.base import ComplianceProvider, ComplianceRecord

# Zoya's ComplianceStatus enum IS this app's own vocabulary as of 2026-11-23
# (CLAUDE.md section 5) -- this app's four statuses were renamed to match Zoya's
# exactly. Kept as an explicit map, not a passthrough: an unrecognized value from
# Zoya must still raise (see get_report below), never be trusted and stored
# as-is just because the strings happen to line up today.
_STATUS_MAP = {
    "COMPLIANT": "COMPLIANT",
    "NON_COMPLIANT": "NON_COMPLIANT",
    "QUESTIONABLE": "QUESTIONABLE",
    "UNRATED": "UNRATED",
}

_QUERY = """
query ZoyaBasicCompliance($symbol: String!) {
  basicCompliance {
    report(symbol: $symbol) {
      symbol
      name
      exchange
      status
      purificationRatio
      reportDate
    }
  }
}
"""


@dataclass(frozen=True)
class ZoyaReport:
    """The full basicCompliance.report shape -- more detail than the generic
    ComplianceRecord carries (CLAUDE.md section 4: "no provider-specific
    fields may leak outside its adapter" -- this is the adapter's own type,
    used only by the Zoya-specific lookup route, never by generic code)."""
    symbol: str
    name: str
    exchange: str
    status: str  # already mapped to this app's four statuses
    purification_ratio: float
    report_date: datetime


class ZoyaApiError(Exception):
    """Any failure calling Zoya's API -- network error, non-2xx response, a
    GraphQL `errors` entry, or an unrecognized status enum value. Deliberately
    never swallowed into "no record": that would be indistinguishable from
    Zoya genuinely having no coverage for the symbol. Same "fail loudly, never
    invent or silently interpolate" principle as CLAUDE.md's must-pass test 6,
    applied here instead of to price data."""


class ZoyaProvider(ComplianceProvider):
    name = "zoya"

    def __init__(self, *, api_key: str, api_url: str, timeout_seconds: float,
                default_validity_days: int):
        self._api_key = api_key
        self._api_url = api_url
        self._timeout_seconds = timeout_seconds
        self._default_validity_days = default_validity_days

    @property
    def is_sandbox(self) -> bool:
        return "sandbox" in self._api_url

    def get_report(self, symbol: str) -> ZoyaReport | None:
        """None means Zoya has no coverage for this symbol -- a legitimate,
        distinct outcome from an error. Raises ZoyaApiError on anything else
        that goes wrong (network failure, non-2xx, a GraphQL `errors` entry,
        an unrecognized status enum, or an unparseable reportDate)."""
        try:
            response = httpx.post(
                self._api_url,
                json={"query": _QUERY, "variables": {"symbol": symbol}},
                headers={"Authorization": self._api_key, "Content-Type": "application/json"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ZoyaApiError(f"Zoya API request failed for {symbol!r}") from exc

        if body.get("errors"):
            raise ZoyaApiError(f"Zoya API returned an error for {symbol!r}: {body['errors']}")

        report = (body.get("data") or {}).get("basicCompliance", {}).get("report")
        if report is None:
            return None

        zoya_status = report.get("status")
        status = _STATUS_MAP.get(zoya_status)
        if status is None:
            raise ZoyaApiError(f"unrecognized Zoya status {zoya_status!r} for {symbol!r}")

        try:
            report_date = datetime.fromisoformat(report["reportDate"].replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ZoyaApiError(f"unparseable Zoya reportDate for {symbol!r}") from exc
        if report_date.tzinfo is None:
            report_date = report_date.replace(tzinfo=timezone.utc)

        return ZoyaReport(
            symbol=report.get("symbol", symbol),
            name=report.get("name", ""),
            exchange=report.get("exchange", ""),
            status=status,
            purification_ratio=report.get("purificationRatio", 0.0),
            report_date=report_date,
        )

    def get_record(self, symbol: str) -> ComplianceRecord | None:
        """ComplianceProvider conformance, for generic/polymorphic callers.
        evidence_url is always None (see module docstring) -- callers must
        never write this straight to compliance_checks without a human
        supplying real evidence first."""
        report = self.get_report(symbol)
        if report is None:
            return None
        return ComplianceRecord(
            symbol=report.symbol,
            status=report.status,
            source="zoya",
            checked_at=report.report_date,
            expires_at=report.report_date + timedelta(days=self._default_validity_days),
            evidence_url=None,
        )
