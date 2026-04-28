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

    client = _build_client()
    set_default_openai_client(client)
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)
    return client


def openrouter_model(model_name: str) -> OpenAIChatCompletionsModel:

    return OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=_build_client(),
    )
