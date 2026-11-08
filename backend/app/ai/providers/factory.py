from app.ai.providers.base import LLMProvider, ProviderNotConfigured
from app.core.config import get_settings

_cached: LLMProvider | None = None
_override: LLMProvider | None = None


def set_provider_override(p: LLMProvider | None) -> None:
    """Test hook / explicit injection."""
    global _override
    _override = p


def get_provider() -> LLMProvider:
    global _cached
    if _override is not None:
        return _override
    if _cached is not None:
        return _cached
    s = get_settings()
    kind = s.ai_provider.strip().lower()
    if kind == "mock":
        if s.env == "production":
            raise ProviderNotConfigured("the mock AI provider is not allowed when SIA_ENV=production")
        from app.ai.providers.mock import MockProvider

        _cached = MockProvider()
    elif kind == "openai":
        from app.ai.providers.openai import OpenAIProvider

        _cached = OpenAIProvider(s.openai_api_key.get_secret_value(), s.ai_model, s.ai_embedding_model, s.openai_base_url)
    else:
        raise ProviderNotConfigured("no AI provider configured (set SIA_AI_PROVIDER)")
    return _cached


def reset_provider_cache() -> None:
    global _cached
    _cached = None
