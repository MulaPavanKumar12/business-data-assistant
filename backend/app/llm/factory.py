"""Build the configured :class:`LLMClient`."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMClient, LLMError

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4o-mini", "ollama": "llama3.1"}


def get_llm_client(settings: Settings) -> LLMClient:
    """Return a client for ``settings.llm_provider`` (anthropic | openai | ollama)."""
    provider = settings.llm_provider.lower()
    model = settings.llm_model or DEFAULT_MODELS.get(provider, "")
    if provider == "anthropic":
        from app.llm.anthropic_client import AnthropicClient

        return AnthropicClient(
            model=model,
            api_key=settings.anthropic_api_key,
            effort=settings.llm_effort,
            enable_fallbacks=settings.llm_enable_fallbacks,
        )
    if provider in ("openai", "ollama"):
        from app.llm.openai_client import OpenAICompatibleClient

        base_url = settings.openai_base_url or ("http://localhost:11434/v1" if provider == "ollama" else None)
        return OpenAICompatibleClient(model=model, api_key=settings.openai_api_key, base_url=base_url)
    raise LLMError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'")
