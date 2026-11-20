"""Pure: no real network call -- anthropic.Anthropic is monkeypatched out
everywhere a real client would otherwise be constructed."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.services.ai_summary_llm import LlmCallError, call_llm

FAKE_KEY = "sk-ant-totally-secret-value-should-never-leak-anywhere"


def _settings(**overrides) -> Settings:
    base = {"database_url": "postgresql+psycopg://u:p@localhost:5432/x"}
    base.update(overrides)
    return Settings(**base)


def test_secret_str_never_appears_in_settings_repr_or_str():
    settings = _settings(ai_api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)


def test_secret_str_get_secret_value_returns_the_real_key():
    settings = _settings(ai_api_key=FAKE_KEY)
    assert settings.ai_api_key.get_secret_value() == FAKE_KEY


def test_empty_key_is_the_default():
    settings = _settings()
    assert settings.ai_api_key.get_secret_value() == ""


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def test_call_llm_returns_text_on_success(monkeypatch):
    fake_response = SimpleNamespace(content=[_text_block("A plain-language summary.")])
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    monkeypatch.setattr("app.services.ai_summary_llm.anthropic.Anthropic", lambda **kw: fake_client)

    text = call_llm(api_key=FAKE_KEY, model="claude-haiku-5-5", timeout_seconds=5.0,
                    allowed_placeholders=frozenset({"trades_count"}))
    assert text == "A plain-language summary."


def test_call_llm_raises_llm_call_error_on_sdk_exception_without_leaking_the_key(monkeypatch):
    fake_client = MagicMock()
    # The exception message itself contains the key -- a realistic worst case
    # (e.g. an SDK error echoing request details) -- our wrapper must not
    # repeat it.
    fake_client.messages.create.side_effect = RuntimeError(f"auth failed for key {FAKE_KEY}")
    monkeypatch.setattr("app.services.ai_summary_llm.anthropic.Anthropic", lambda **kw: fake_client)

    with pytest.raises(LlmCallError) as exc_info:
        call_llm(api_key=FAKE_KEY, model="claude-haiku-5-5", timeout_seconds=5.0,
                allowed_placeholders=frozenset({"trades_count"}))

    assert FAKE_KEY not in str(exc_info.value)


def test_call_llm_raises_on_empty_response(monkeypatch):
    fake_response = SimpleNamespace(content=[_text_block("   ")])
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    monkeypatch.setattr("app.services.ai_summary_llm.anthropic.Anthropic", lambda **kw: fake_client)

    with pytest.raises(LlmCallError):
        call_llm(api_key=FAKE_KEY, model="claude-haiku-5-5", timeout_seconds=5.0,
                allowed_placeholders=frozenset({"trades_count"}))
