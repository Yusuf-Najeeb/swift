"""Tests for the OpenRouter provider wiring."""

from __future__ import annotations

import pytest

from backend.agents import providers
from backend.agents.providers import (
    _build_client,
    configure_openrouter,
    openrouter_model,
)
from backend.config import get_settings


@pytest.fixture(autouse=True)
def _clear_client_cache() -> None:
    _build_client.cache_clear()
    yield
    _build_client.cache_clear()


def test_configure_openrouter_uses_openrouter_base_url() -> None:
    client = configure_openrouter()
    settings = get_settings()

    # httpx/openai exposes the configured base URL on the client.
    assert str(client.base_url).rstrip("/") == settings.openrouter_base_url.rstrip("/")
    assert client.api_key == settings.openrouter_api_key


def test_openrouter_model_wraps_configured_client() -> None:
    model = openrouter_model("openai/gpt-4o-mini")

    # The Agents SDK stores the model name on the wrapper.
    assert getattr(model, "model", None) == "openai/gpt-4o-mini"


def test_configure_openrouter_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_set_default(client):  # type: ignore[no-untyped-def]
        calls.append(client)

    monkeypatch.setattr(providers, "set_default_openai_client", fake_set_default)
    monkeypatch.setattr(providers, "set_default_openai_api", lambda *_a, **_k: None)
    monkeypatch.setattr(providers, "set_tracing_disabled", lambda *_a, **_k: None)

    first = configure_openrouter()
    second = configure_openrouter()

    assert first is second  # client is cached
    assert len(calls) == 2  # set_default is re-applied, but client identity is stable
