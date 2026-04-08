"""Shared singletons for all router sub-modules."""

import asyncio
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

# Expert model client — provider determined by LEAN_AI_EXPERT_LLM_PROVIDER
expert_llm_client: LLMClient | None = None
_expert_p = (settings.expert_llm_provider or "").lower()

# Empty = backwards-compat: Ollama expert only when primary provider is also Ollama
if not _expert_p:
    if settings.llm_provider.lower() == "ollama" and settings.ollama_model_expert:
        _expert_p = "ollama"

if _expert_p == "openai":
    _expert_model = settings.openai_expert_model or settings.openai_model
    if _expert_model and settings.openai_api_key:
        try:
            from lean_ai.llm.provider_openai import OpenAIProvider as _OpenAIProvider
            _expert_provider = _OpenAIProvider(
                api_key=settings.openai_api_key,
                model=_expert_model,
                base_url=settings.openai_base_url or None,
                temperature=settings.openai_temperature,
                context_window=settings.openai_context_window,
                max_tokens=settings.openai_max_tokens,
                enable_thinking=settings.enable_thinking_expert,
            )
            expert_llm_client = LLMClient(provider=_expert_provider)
            logger.info("Expert model enabled (OpenAI): %s", _expert_model)
        except Exception as e:
            logger.warning("Could not create expert LLM client (OpenAI): %s", e)
    else:
        logger.warning(
            "Expert provider is 'openai' but %s — expert model disabled",
            "OPENAI_API_KEY is not set" if not settings.openai_api_key
            else "no model name resolved",
        )

elif _expert_p == "anthropic":
    _expert_model = settings.anthropic_expert_model or settings.anthropic_model
    if _expert_model and settings.anthropic_api_key:
        try:
            from lean_ai.llm.provider_anthropic import AnthropicProvider as _AnthropicProvider
            _expert_provider = _AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=_expert_model,
                temperature=settings.anthropic_temperature,
                context_window=settings.anthropic_context_window,
                max_tokens=settings.anthropic_max_tokens,
                enable_thinking=settings.enable_thinking_expert,
            )
            expert_llm_client = LLMClient(provider=_expert_provider)
            logger.info("Expert model enabled (Anthropic): %s", _expert_model)
        except Exception as e:
            logger.warning("Could not create expert LLM client (Anthropic): %s", e)
    else:
        logger.warning(
            "Expert provider is 'anthropic' but %s — expert model disabled",
            "ANTHROPIC_API_KEY is not set" if not settings.anthropic_api_key
            else "no model name resolved",
        )

elif _expert_p == "serve":
    _expert_model = settings.serve_expert_model or settings.serve_model
    if _expert_model and settings.serve_api_key:
        try:
            from lean_ai.llm.provider_openai import OpenAIProvider as _ServeExpert
            _expert_provider = _ServeExpert(
                api_key=settings.serve_api_key,
                model=_expert_model,
                base_url=f"{settings.serve_url.rstrip('/')}/v1",
                temperature=settings.serve_temperature,
                context_window=settings.serve_context_window,
                max_tokens=settings.serve_max_tokens,
                enable_thinking=settings.enable_thinking_expert,
            )
            expert_llm_client = LLMClient(provider=_expert_provider)
            logger.info("Expert model enabled (Serve): %s", _expert_model)
        except Exception as e:
            logger.warning("Could not create expert LLM client (Serve): %s", e)
    else:
        logger.warning(
            "Expert provider is 'serve' but %s — expert model disabled",
            "SERVE_API_KEY is not set" if not settings.serve_api_key
            else "no model name resolved",
        )

elif _expert_p == "gemini":
    _expert_model = settings.gemini_expert_model or settings.gemini_model
    if _expert_model and settings.gemini_api_key:
        try:
            from lean_ai.llm.provider_gemini import GeminiProvider as _GeminiExpert
            _expert_provider = _GeminiExpert(
                api_key=settings.gemini_api_key,
                model=_expert_model,
                temperature=settings.gemini_temperature,
                context_window=settings.gemini_context_window,
                max_tokens=settings.gemini_max_tokens,
                enable_thinking=settings.enable_thinking_expert,
            )
            expert_llm_client = LLMClient(provider=_expert_provider)
            logger.info("Expert model enabled (Gemini): %s", _expert_model)
        except Exception as e:
            logger.warning("Could not create expert LLM client (Gemini): %s", e)
    else:
        logger.warning(
            "Expert provider is 'gemini' but %s — expert model disabled",
            "GEMINI_API_KEY is not set" if not settings.gemini_api_key
            else "no model name resolved",
        )

elif _expert_p == "ollama" and settings.ollama_model_expert:
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
            enable_thinking=settings.enable_thinking_expert,
        )
        expert_llm_client = LLMClient(provider=_expert_provider)
        logger.info(
            "Expert model enabled (Ollama): %s (ctx=%s, max_tokens=%s)",
            settings.ollama_model_expert,
            settings.ollama_expert_context_window or settings.ollama_context_window,
            settings.ollama_expert_max_tokens,
        )
    except Exception as e:
        logger.warning("Could not create expert LLM client (Ollama): %s", e)
        expert_llm_client = None

# Request model client — for /request mode (open-ended tasks)
request_llm_client: LLMClient | None = None
_request_p = (settings.request_llm_provider or "").lower()

# Empty = auto-detect: Ollama request only when primary provider is also Ollama
if not _request_p:
    if settings.llm_provider.lower() == "ollama" and settings.ollama_model_request:
        _request_p = "ollama"

if _request_p == "openai":
    _request_model = settings.openai_request_model or settings.openai_model
    if _request_model and settings.openai_api_key:
        try:
            from lean_ai.llm.provider_openai import OpenAIProvider as _ReqOpenAI
            _request_provider = _ReqOpenAI(
                api_key=settings.openai_api_key,
                model=_request_model,
                base_url=settings.openai_base_url or None,
                temperature=settings.openai_temperature,
                context_window=settings.openai_context_window,
                max_tokens=settings.openai_max_tokens,
                enable_thinking=settings.enable_thinking_request,
            )
            request_llm_client = LLMClient(
                provider=_request_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Request model enabled (OpenAI): %s", _request_model)
        except Exception as e:
            logger.warning("Could not create request LLM client (OpenAI): %s", e)
    else:
        logger.warning(
            "Request provider is 'openai' but %s — request model disabled",
            "OPENAI_API_KEY is not set" if not settings.openai_api_key
            else "no model name resolved",
        )

elif _request_p == "anthropic":
    _request_model = settings.anthropic_request_model or settings.anthropic_model
    if _request_model and settings.anthropic_api_key:
        try:
            from lean_ai.llm.provider_anthropic import AnthropicProvider as _ReqAnthropic
            _request_provider = _ReqAnthropic(
                api_key=settings.anthropic_api_key,
                model=_request_model,
                temperature=settings.anthropic_temperature,
                context_window=settings.anthropic_context_window,
                max_tokens=settings.anthropic_max_tokens,
                enable_thinking=settings.enable_thinking_request,
            )
            request_llm_client = LLMClient(
                provider=_request_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Request model enabled (Anthropic): %s", _request_model)
        except Exception as e:
            logger.warning("Could not create request LLM client (Anthropic): %s", e)
    else:
        logger.warning(
            "Request provider is 'anthropic' but %s — request model disabled",
            "ANTHROPIC_API_KEY is not set" if not settings.anthropic_api_key
            else "no model name resolved",
        )

elif _request_p == "serve":
    _request_model = settings.serve_request_model or settings.serve_model
    if _request_model and settings.serve_api_key:
        try:
            from lean_ai.llm.provider_openai import OpenAIProvider as _ReqServe
            _request_provider = _ReqServe(
                api_key=settings.serve_api_key,
                model=_request_model,
                base_url=f"{settings.serve_url.rstrip('/')}/v1",
                temperature=settings.serve_temperature,
                context_window=settings.serve_context_window,
                max_tokens=settings.serve_max_tokens,
                enable_thinking=settings.enable_thinking_request,
            )
            request_llm_client = LLMClient(
                provider=_request_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Request model enabled (Serve): %s", _request_model)
        except Exception as e:
            logger.warning("Could not create request LLM client (Serve): %s", e)
    else:
        logger.warning(
            "Request provider is 'serve' but %s — request model disabled",
            "SERVE_API_KEY is not set" if not settings.serve_api_key
            else "no model name resolved",
        )

elif _request_p == "gemini":
    _request_model = settings.gemini_request_model or settings.gemini_model
    if _request_model and settings.gemini_api_key:
        try:
            from lean_ai.llm.provider_gemini import GeminiProvider as _ReqGemini
            _request_provider = _ReqGemini(
                api_key=settings.gemini_api_key,
                model=_request_model,
                temperature=settings.gemini_temperature,
                context_window=settings.gemini_context_window,
                max_tokens=settings.gemini_max_tokens,
                enable_thinking=settings.enable_thinking_request,
            )
            request_llm_client = LLMClient(
                provider=_request_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Request model enabled (Gemini): %s", _request_model)
        except Exception as e:
            logger.warning("Could not create request LLM client (Gemini): %s", e)
    else:
        logger.warning(
            "Request provider is 'gemini' but %s — request model disabled",
            "GEMINI_API_KEY is not set" if not settings.gemini_api_key
            else "no model name resolved",
        )

elif _request_p == "ollama" and settings.ollama_model_request:
    try:
        _request_provider = OllamaProvider(
            ollama_url=settings.ollama_url,
            model=settings.ollama_model_request,
            max_tokens=settings.ollama_request_max_tokens,
            context_window=settings.ollama_request_context_window or settings.ollama_context_window,
            temperature=settings.effective_request_temperature,
            top_p=settings.effective_request_top_p,
            top_k=settings.effective_request_top_k,
            repeat_penalty=settings.effective_request_repeat_penalty,
            enable_thinking=settings.enable_thinking_request,
        )
        request_llm_client = LLMClient(
            provider=_request_provider,
            concurrency_semaphore=_llm_semaphore,
        )
        logger.info(
            "Request model enabled (Ollama): %s (ctx=%s, max_tokens=%s)",
            settings.ollama_model_request,
            settings.ollama_request_context_window or settings.ollama_context_window,
            settings.ollama_request_max_tokens,
        )
    except Exception as e:
        logger.warning("Could not create request LLM client (Ollama): %s", e)
        request_llm_client = None

# Worker model client — lightweight auxiliary tasks (summarization, compression)
worker_llm_client: LLMClient | None = None
_worker_p = (settings.worker_llm_provider or "").lower()

# Empty = auto-detect: Ollama worker only when primary provider is also Ollama
if not _worker_p:
    if settings.llm_provider.lower() == "ollama" and settings.ollama_model_worker:
        _worker_p = "ollama"

if _worker_p == "openai":
    _worker_model = settings.openai_worker_model or settings.openai_model
    if _worker_model and settings.openai_api_key:
        try:
            from lean_ai.llm.provider_openai import OpenAIProvider as _WrkOpenAI
            _worker_provider = _WrkOpenAI(
                api_key=settings.openai_api_key,
                model=_worker_model,
                base_url=settings.openai_base_url or None,
                temperature=settings.openai_temperature,
                context_window=settings.openai_context_window,
                max_tokens=settings.openai_max_tokens,
                enable_thinking=settings.enable_thinking_worker,
            )
            worker_llm_client = LLMClient(
                provider=_worker_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Worker model enabled (OpenAI): %s", _worker_model)
        except Exception as e:
            logger.warning("Could not create worker LLM client (OpenAI): %s", e)

elif _worker_p == "anthropic":
    _worker_model = settings.anthropic_worker_model or settings.anthropic_model
    if _worker_model and settings.anthropic_api_key:
        try:
            from lean_ai.llm.provider_anthropic import AnthropicProvider as _WrkAnthropic
            _worker_provider = _WrkAnthropic(
                api_key=settings.anthropic_api_key,
                model=_worker_model,
                temperature=settings.anthropic_temperature,
                context_window=settings.anthropic_context_window,
                max_tokens=settings.anthropic_max_tokens,
                enable_thinking=settings.enable_thinking_worker,
            )
            worker_llm_client = LLMClient(
                provider=_worker_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Worker model enabled (Anthropic): %s", _worker_model)
        except Exception as e:
            logger.warning("Could not create worker LLM client (Anthropic): %s", e)

elif _worker_p == "serve":
    _worker_model = settings.serve_worker_model or settings.serve_model
    if _worker_model and settings.serve_api_key:
        try:
            from lean_ai.llm.provider_openai import OpenAIProvider as _WrkServe
            _worker_provider = _WrkServe(
                api_key=settings.serve_api_key,
                model=_worker_model,
                base_url=f"{settings.serve_url.rstrip('/')}/v1",
                temperature=settings.serve_temperature,
                context_window=settings.serve_context_window,
                max_tokens=settings.serve_max_tokens,
                enable_thinking=settings.enable_thinking_worker,
            )
            worker_llm_client = LLMClient(
                provider=_worker_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Worker model enabled (Serve): %s", _worker_model)
        except Exception as e:
            logger.warning("Could not create worker LLM client (Serve): %s", e)

elif _worker_p == "gemini":
    _worker_model = settings.gemini_worker_model or settings.gemini_model
    if _worker_model and settings.gemini_api_key:
        try:
            from lean_ai.llm.provider_gemini import GeminiProvider as _WrkGemini
            _worker_provider = _WrkGemini(
                api_key=settings.gemini_api_key,
                model=_worker_model,
                temperature=settings.gemini_temperature,
                context_window=settings.gemini_context_window,
                max_tokens=settings.gemini_max_tokens,
                enable_thinking=settings.enable_thinking_worker,
            )
            worker_llm_client = LLMClient(
                provider=_worker_provider,
                concurrency_semaphore=_llm_semaphore,
            )
            logger.info("Worker model enabled (Gemini): %s", _worker_model)
        except Exception as e:
            logger.warning("Could not create worker LLM client (Gemini): %s", e)

elif _worker_p == "ollama" and settings.ollama_model_worker:
    try:
        _worker_provider = OllamaProvider(
            ollama_url=settings.ollama_url,
            model=settings.ollama_model_worker,
            max_tokens=settings.ollama_worker_max_tokens,
            context_window=settings.ollama_worker_context_window or settings.ollama_context_window,
            temperature=settings.effective_worker_temperature,
            top_p=settings.effective_worker_top_p,
            top_k=settings.effective_worker_top_k,
            repeat_penalty=settings.effective_worker_repeat_penalty,
            enable_thinking=settings.enable_thinking_worker,
        )
        worker_llm_client = LLMClient(
            provider=_worker_provider,
            concurrency_semaphore=_llm_semaphore,
        )
        logger.info(
            "Worker model enabled (Ollama): %s (ctx=%s, max_tokens=%s)",
            settings.ollama_model_worker,
            settings.ollama_worker_context_window or settings.ollama_context_window,
            settings.ollama_worker_max_tokens,
        )
    except Exception as e:
        logger.warning("Could not create worker LLM client (Ollama): %s", e)
        worker_llm_client = None
