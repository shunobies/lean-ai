"""Shared singletons for all router sub-modules."""

import asyncio
import logging

from lean_ai.config import settings
from lean_ai.llm.client import OllamaProvider
from lean_ai.llm.facade import LLMClient
from lean_ai.llm.refiner import PromptRefiner
from lean_ai.routers.client_factory import RoleConfig, create_role_client

logger = logging.getLogger(__name__)


def _create_provider():
    """Factory: create the LLM provider based on settings.llm_provider."""
    provider = settings.llm_provider.lower()

    if provider == "ollama":
        return OllamaProvider(
            ollama_url=settings.ollama_url,
            model=settings.ollama_model,
            max_tokens=settings.ollama_max_tokens,
            context_window=settings.ollama_context_window,
            temperature=settings.ollama_temperature,
            enable_thinking=settings.enable_thinking,
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
            enable_thinking=settings.enable_thinking,
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
            enable_thinking=settings.enable_thinking,
        )

    if provider == "serve":
        if not settings.serve_api_key:
            raise ValueError(
                "LEAN_AI_SERVE_API_KEY must be set when LEAN_AI_LLM_PROVIDER=serve"
            )
        if not settings.serve_model:
            raise ValueError(
                "LEAN_AI_SERVE_MODEL must be set when LEAN_AI_LLM_PROVIDER=serve"
            )
        from lean_ai.llm.provider_openai import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.serve_api_key,
            model=settings.serve_model,
            max_tokens=settings.serve_max_tokens,
            context_window=settings.serve_context_window,
            temperature=settings.serve_temperature,
            base_url=f"{settings.serve_url.rstrip('/')}/v1",
            enable_thinking=settings.enable_thinking,
        )

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError(
                "LEAN_AI_GEMINI_API_KEY must be set when LEAN_AI_LLM_PROVIDER=gemini"
            )
        from lean_ai.llm.provider_gemini import GeminiProvider
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            max_tokens=settings.gemini_max_tokens,
            context_window=settings.gemini_context_window,
            temperature=settings.gemini_temperature,
            enable_thinking=settings.enable_thinking,
        )

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. "
        f"Supported: ollama, openai, anthropic, gemini, serve"
    )


_llm_semaphore: asyncio.Semaphore | None = (
    asyncio.Semaphore(settings.num_parallel) if settings.num_parallel > 1 else None
)

llm_client = LLMClient(
    provider=_create_provider(),
    concurrency_semaphore=_llm_semaphore,
)

# Inline prediction client — always Ollama-backed
_inline_client: LLMClient = (
    LLMClient(provider=OllamaProvider(
        ollama_url=settings.effective_inline_url,
        model=settings.inline_model,
        max_tokens=settings.inline_max_tokens,
        context_window=settings.inline_context_window,
        enable_thinking=False,
    ))
    if settings.inline_model
    else llm_client
)

# Local refiner — active only when using a cloud provider
refiner: PromptRefiner | None = None
if (
    settings.llm_provider.lower() in ("openai", "anthropic", "gemini", "serve")
    and settings.enable_refiner
):
    try:
        _refiner_provider = OllamaProvider(
            ollama_url=settings.effective_refiner_url,
            model=settings.effective_refiner_model,
            max_tokens=settings.ollama_max_tokens,
            context_window=settings.ollama_context_window,
            temperature=settings.ollama_temperature,
            enable_thinking=settings.enable_thinking,
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

# Expert model client
expert_llm_client: LLMClient | None = create_role_client(
    RoleConfig(
        role_name="Expert",
        provider_setting=settings.expert_llm_provider or "",
        enable_thinking=settings.enable_thinking_expert,
        ollama_model=settings.ollama_model_expert or "",
        ollama_max_tokens=settings.ollama_expert_max_tokens,
        ollama_context_window=(
            settings.ollama_expert_context_window or settings.ollama_context_window
        ),
        ollama_temperature=settings.effective_expert_temperature,
        ollama_top_p=settings.effective_expert_top_p,
        ollama_top_k=settings.effective_expert_top_k,
        ollama_repeat_penalty=settings.effective_expert_repeat_penalty,
        openai_model=settings.openai_expert_model or "",
        anthropic_model=settings.anthropic_expert_model or "",
        gemini_model=settings.gemini_expert_model or "",
        serve_model=settings.serve_expert_model or "",
        use_semaphore=False,
    ),
    semaphore=_llm_semaphore,
)

# Request model client — for /request mode (open-ended tasks)
request_llm_client: LLMClient | None = create_role_client(
    RoleConfig(
        role_name="Request",
        provider_setting=settings.request_llm_provider or "",
        enable_thinking=settings.enable_thinking_request,
        ollama_model=settings.ollama_model_request or "",
        ollama_max_tokens=settings.ollama_request_max_tokens,
        ollama_context_window=(
            settings.ollama_request_context_window or settings.ollama_context_window
        ),
        ollama_temperature=settings.effective_request_temperature,
        ollama_top_p=settings.effective_request_top_p,
        ollama_top_k=settings.effective_request_top_k,
        ollama_repeat_penalty=settings.effective_request_repeat_penalty,
        openai_model=settings.openai_request_model or "",
        anthropic_model=settings.anthropic_request_model or "",
        gemini_model=settings.gemini_request_model or "",
        serve_model=settings.serve_request_model or "",
        use_semaphore=True,
    ),
    semaphore=_llm_semaphore,
)

# Worker model client — lightweight auxiliary tasks (summarization, compression)
worker_llm_client: LLMClient | None = create_role_client(
    RoleConfig(
        role_name="Worker",
        provider_setting=settings.worker_llm_provider or "",
        enable_thinking=settings.enable_thinking_worker,
        ollama_model=settings.ollama_model_worker or "",
        ollama_max_tokens=settings.ollama_worker_max_tokens,
        ollama_context_window=(
            settings.ollama_worker_context_window or settings.ollama_context_window
        ),
        ollama_temperature=settings.effective_worker_temperature,
        ollama_top_p=settings.effective_worker_top_p,
        ollama_top_k=settings.effective_worker_top_k,
        ollama_repeat_penalty=settings.effective_worker_repeat_penalty,
        openai_model=settings.openai_worker_model or "",
        anthropic_model=settings.anthropic_worker_model or "",
        gemini_model=settings.gemini_worker_model or "",
        serve_model=settings.serve_worker_model or "",
        use_semaphore=True,
    ),
    semaphore=_llm_semaphore,
)
