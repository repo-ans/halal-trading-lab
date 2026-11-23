"""Pure -- no DB, no real network call. httpx.post is monkeypatched directly
rather than using a live server, same spirit as test_ai_summary_llm.py mocking
anthropic.Anthropic."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.providers.compliance.zoya import ZoyaApiError, ZoyaProvider

API_URL = "https://sandbox-api.zoya.finance/graphql"


def _provider(**overrides) -> ZoyaProvider:
    kwargs = dict(api_key="k", api_url=API_URL, timeout_seconds=5.0, default_validity_days=90)
    kwargs.update(overrides)
    return ZoyaProvider(**kwargs)


def _fake_response(json_body: dict, status_code: int = 200):
    def _raise_for_status():
        if status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("boom", request=None, response=None)
    return SimpleNamespace(json=lambda: json_body, raise_for_status=_raise_for_status)


@pytest.mark.parametrize("zoya_status,expected", [
    ("COMPLIANT", "COMPLIANT"),
    ("NON_COMPLIANT", "NON_COMPLIANT"),
    ("QUESTIONABLE", "QUESTIONABLE"),
    ("UNRATED", "UNRATED"),
])
def test_status_mapping(monkeypatch, zoya_status, expected):
    # This app's status vocabulary IS Zoya's own enum (CLAUDE.md section 5,
    # renamed 2026-11-23) -- the mapping is an identity, but it's still
    # explicit, real code (app/providers/compliance/zoya.py's _STATUS_MAP),
    # not an unchecked passthrough: an unrecognized value must still raise
    # (see test_unrecognized_status_raises_never_silently_dropped below).
    body = {"data": {"basicCompliance": {"report": {
        "symbol": "AAPL", "name": "Apple Inc", "exchange": "XNGS",
        "status": zoya_status, "purificationRatio": 0.0,
        "reportDate": "2026-02-18T00:00:00.000Z",
    }}}}
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_response(body))

    provider = _provider()
    report = provider.get_report("AAPL")

    assert report is not None
    assert report.status == expected
    assert report.symbol == "AAPL"
    assert report.report_date == datetime(2026, 2, 18, tzinfo=timezone.utc)

    record = provider.get_record("AAPL")
    assert record is not None
    assert record.status == expected
    assert record.source == "zoya"
    assert record.checked_at == datetime(2026, 2, 18, tzinfo=timezone.utc)
    assert record.expires_at == datetime(2026, 5, 19, tzinfo=timezone.utc)  # +90 days
    # CLAUDE.md section 5: Zoya's API gives no evidence link -- never fill
    # this in with something that only looks like real evidence.
    assert record.evidence_url is None


def test_no_coverage_returns_none_not_an_error(monkeypatch):
    body = {"data": {"basicCompliance": {"report": None}}}
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_response(body))

    assert _provider().get_report("ZZZZZ") is None
    assert _provider().get_record("ZZZZZ") is None


def test_graphql_errors_raise_not_silently_treated_as_no_coverage(monkeypatch):
    body = {"errors": [{"message": "not authorized"}]}
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_response(body))

    with pytest.raises(ZoyaApiError):
        _provider().get_report("AAPL")


def test_network_failure_raises(monkeypatch):
    def _raise(*a, **k):
        import httpx
        raise httpx.ConnectError("no route to host")
    monkeypatch.setattr("httpx.post", _raise)

    with pytest.raises(ZoyaApiError):
        _provider().get_report("AAPL")


def test_unrecognized_status_raises_never_silently_dropped(monkeypatch):
    body = {"data": {"basicCompliance": {"report": {
        "symbol": "AAPL", "name": "x", "exchange": "x", "status": "SOMETHING_NEW",
        "purificationRatio": 0.0, "reportDate": "2026-02-18T00:00:00.000Z",
    }}}}
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_response(body))

    with pytest.raises(ZoyaApiError):
        _provider().get_report("AAPL")


def test_unparseable_report_date_raises(monkeypatch):
    body = {"data": {"basicCompliance": {"report": {
        "symbol": "AAPL", "name": "x", "exchange": "x", "status": "COMPLIANT",
        "purificationRatio": 0.0, "reportDate": "not-a-date",
    }}}}
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_response(body))

    with pytest.raises(ZoyaApiError):
        _provider().get_report("AAPL")


def test_is_sandbox_detected_from_url():
    assert _provider(api_url="https://sandbox-api.zoya.finance/graphql").is_sandbox is True
    assert _provider(api_url="https://api.zoya.finance/graphql").is_sandbox is False


def test_auth_header_is_the_raw_key_no_bearer_prefix(monkeypatch):
    """Confirmed against the real sandbox API: Authorization is the raw key
    string, not "Bearer <key>" -- a wrong header format here would silently
    turn every lookup into an auth failure."""
    captured = {}

    def _post(url, json, headers, timeout):
        captured["headers"] = headers
        return _fake_response({"data": {"basicCompliance": {"report": None}}})

    monkeypatch.setattr("httpx.post", _post)
    _provider(api_key="sandbox-abc123").get_report("AAPL")

    assert captured["headers"]["Authorization"] == "sandbox-abc123"
