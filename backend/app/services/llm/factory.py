"""Factory that returns the configured LLM provider instance."""
from __future__ import annotations

from app.config import Settings, get_settings
from app.services.llm.base import LLMProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider


def get_llm_provider(settings: Settings | None = None, provider_override: str | None = None) -> LLMProvider:
    settings = settings or get_settings()
    provider = provider_override or settings.LLM_PROVIDER

    if provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.OPENAI_COMPATIBLE_BASE_URL,
            api_key=settings.OPENAI_COMPATIBLE_API_KEY,
            model=settings.OPENAI_COMPATIBLE_MODEL,
        )
    if provider == "ollama":
        return OllamaProvider(base_url=settings.OLLAMA_BASE_URL, model=settings.OLLAMA_MODEL)

    raise ValueError(f"Unknown LLM provider '{provider}'. Expected 'ollama' or 'openai_compatible'.")
