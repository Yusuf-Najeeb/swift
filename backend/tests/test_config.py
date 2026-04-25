"""Tests for backend.config."""

from __future__ import annotations

import pytest

from backend.config import Settings, get_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-abc")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.openrouter_api_key == "sk-abc"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.app_name == "Swift Writer"

    # Modern default model choices per scaffolding plan.
    assert settings.orchestrator_model == "anthropic/claude-sonnet-4.5"
    assert settings.writer_model == "openai/gpt-4o-mini"
    assert settings.evaluator_model == "openai/gpt-4o"
    assert settings.image_agent_model == "openai/gpt-4o-mini"

    # Optional credential — absent by default.
    assert settings.serper_api_key is None
    # MCP feature flags default ON, but Serper silently no-ops without
    # SERPER_API_KEY, so defaults are safe.
    assert settings.writer_mcp_fetch_enabled is True
    assert settings.evaluator_mcp_fetch_enabled is True
    assert settings.evaluator_mcp_serper_enabled is True

    # MCP server (Step 6) defaults: enabled, mounted at /mcp, open.
    # ``bearer_token`` intentionally unset so localhost dev Just
    # Works; production deployments must set it.
    assert settings.mcp_server_enabled is True
    assert settings.mcp_server_mount_path == "/mcp"
    assert settings.mcp_server_bearer_token is None

    # SSE streaming (Step 7) default: 15s keep-alive comments.
    assert settings.sse_keep_alive_seconds == 15.0


def test_model_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-abc")
    monkeypatch.setenv("SWIFT_WRITER_MODEL", "x-ai/grok-4")
    monkeypatch.setenv("SWIFT_ORCHESTRATOR_MODEL", "anthropic/claude-opus-4.5")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.writer_model == "x-ai/grok-4"
    assert settings.orchestrator_model == "anthropic/claude-opus-4.5"


def test_cors_origins_from_comma_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-abc")
    monkeypatch.setenv(
        "SWIFT_CORS_ORIGINS",
        "http://localhost:3000, https://swift.example.com",
    )

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == [
        "http://localhost:3000",
        "https://swift.example.com",
    ]


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(Exception):
        Settings(_env_file=None)  # type: ignore[call-arg]
