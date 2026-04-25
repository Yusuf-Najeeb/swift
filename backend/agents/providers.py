"""OpenRouter wiring for the OpenAI Agents SDK.

OpenRouter speaks the OpenAI Chat Completions API, so we point an
``AsyncOpenAI`` client at it and register that client as the Agents SDK's
default. We also force the SDK to use the Chat Completions endpoint
(OpenRouter does not support the new Responses API) and disable tracing
so the SDK does not try to call OpenAI directly for telemetry.
"""

from __future__ import annotations

from functools import lru_cache

from agents import (
    OpenAIChatCompletionsModel,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from backend.config import get_settings


@lru_cache
def _build_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )


def configure_openrouter() -> AsyncOpenAI:
    """Register OpenRouter as the Agents SDK default client.

    Safe to call multiple times; the underlying OpenAI client is cached.
    """

    client = _build_client()
    set_default_openai_client(client)
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)
    return client


def openrouter_model(model_name: str) -> OpenAIChatCompletionsModel:
    """Build a model object Agents can pass to OpenRouter.

    Example:
        writer = Agent(name="Writer", model=openrouter_model("openai/gpt-4o-mini"))
    """

    return OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=_build_client(),
    )
