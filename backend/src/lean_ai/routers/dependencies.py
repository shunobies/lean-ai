"""Shared singletons for all router sub-modules."""

import logging

from lean_ai.config import settings
from lean_ai.llm.base import LLMProvider
from lean_ai.llm.client import OllamaProvider
from lean_ai.llm.facade import LLMClient
from lean_ai.llm.refiner import PromptRefiner

logger = logging.getLogger(__name__)


def _create_provider() -> LLMProvider:
    """Factory: create the LLM provider based on settings.llm_provider."""
    provider = settings.llm_provider.lower()

    if provider == "ollama":
        return OllamaProvider(
            ollama_url=settings.ollama_url,
            model=settings.ollama_model,
            max_tokens=settings.ollama_max_tokens,
            context_window=settings.ollama_context_window,
            temperature=settings.ollama_temperature,
        )

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError(
                "LEAN_AI_OPENAI_API_KEY must be set when LEAN_AI_LLM_PROVIDER=openai"
            )
        from lean_ai.llm.provider_openai import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_tokens=settings.openai_max_tokens,
            context_window=settings.openai_context_window,
            temperature=settings.openai_temperature,
            base_url=settings.openai_base_url or None,
        )

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError(
                "LEAN_AI_ANTHROPIC_API_KEY must be set when LEAN_AI_LLM_PROVIDER=anthropic"
            )
        from lean_ai.llm.provider_anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            context_window=settings.anthropic_context_window,
            temperature=settings.anthropic_temperature,
        )

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. "
        f"Supported: ollama, openai, anthropic"
    )


llm_client = LLMClient(provider=_create_provider())

# Inline prediction client — always Ollama-backed
_inline_client: LLMClient = (
    LLMClient(provider=OllamaProvider(
        ollama_url=settings.effective_inline_url,
        model=settings.inline_model,
        max_tokens=settings.inline_max_tokens,
        context_window=settings.inline_context_window,
    ))
    if settings.inline_model
    else llm_client
)

# Local refiner — active only when using a cloud provider
refiner: PromptRefiner | None = None
if (
    settings.llm_provider.lower() in ("openai", "anthropic")
    and settings.enable_refiner
):
    try:
        _refiner_provider = OllamaProvider(
            ollama_url=settings.effective_refiner_url,
            model=settings.effective_refiner_model,
            max_tokens=settings.ollama_max_tokens,
            context_window=settings.ollama_context_window,
            temperature=settings.ollama_temperature,
        )
        refiner = PromptRefiner(
            ollama_provider=_refiner_provider,
            enable_knowledge=settings.refiner_enable_knowledge,
            enable_privacy=settings.refiner_enable_privacy,
            knowledge_chunks=settings.refiner_knowledge_chunks,
            timeout=settings.refiner_timeout,
        )
        logger.info(
            "Local refiner enabled (model=%s, knowledge=%s, privacy=%s)",
            settings.effective_refiner_model,
            settings.refiner_enable_knowledge,
            settings.refiner_enable_privacy,
        )
    except Exception:
        logger.warning("Could not create refiner — Ollama may be unavailable")
        refiner = None

# Expert model client — active only when expert model is configured AND provider is Ollama
expert_llm_client: LLMClient | None = None
if settings.llm_provider.lower() == "ollama" and settings.ollama_model_expert:
    try:
        _expert_provider = OllamaProvider(
            ollama_url=settings.ollama_url,
            model=settings.ollama_model_expert,
            max_tokens=settings.ollama_expert_max_tokens,
            context_window=settings.ollama_expert_context_window or settings.ollama_context_window,
            temperature=settings.effective_expert_temperature,
            top_p=settings.effective_expert_top_p,
            top_k=settings.effective_expert_top_k,
            repeat_penalty=settings.effective_expert_repeat_penalty,
        )
        expert_llm_client = LLMClient(provider=_expert_provider)
        logger.info(
            "Expert model enabled: %s (ctx=%s, max_tokens=%s)",
            settings.ollama_model_expert,
            settings.ollama_expert_context_window or settings.ollama_context_window,
            settings.ollama_expert_max_tokens,
        )
    except Exception:
        logger.warning("Could not create expert LLM client — Ollama may be unavailable")
        expert_llm_client = None
