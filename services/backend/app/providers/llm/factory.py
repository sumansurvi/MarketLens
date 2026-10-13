"""Select the configured LLM provider (fixture by default; OpenAI when configured)."""

from __future__ import annotations

from marketlens_core.config import Settings

from app.providers.llm.base import LLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_mode == "openai":
        from app.providers.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    from app.providers.llm.fixture_provider import FixtureLLMProvider

    return FixtureLLMProvider()
