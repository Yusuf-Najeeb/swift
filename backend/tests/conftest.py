"""Shared pytest fixtures.

For the offline suite we inject a dummy OpenRouter API key both at
module load (so modules that instantiate ``Settings`` at import time can
be imported during test collection) and per-test via an autouse fixture
(so tests that clear and rebuild settings via monkeypatch keep working).

When ``OPENROUTER_LIVE=1`` we step out of the way so the real key from
``.env`` (or the shell) reaches the Agents SDK untouched. The
``lru_cache`` on ``get_settings`` is cleared around every test so env
overrides take effect deterministically.
"""

from __future__ import annotations

import os

import pytest

LIVE = os.getenv("OPENROUTER_LIVE") == "1"

if not LIVE:
    os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-dummy")


@pytest.fixture(autouse=True)
def _default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not LIVE:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-dummy")

    from backend.agents.providers import _build_client
    from backend.config import get_settings

    get_settings.cache_clear()
    _build_client.cache_clear()
    yield
    get_settings.cache_clear()
    _build_client.cache_clear()
