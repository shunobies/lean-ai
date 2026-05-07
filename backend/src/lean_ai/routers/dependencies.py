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
            top_p=settings.ollama_top_p,
            top_k=settings.ollama_top_k,
            repeat_penalty=settings.ollama_repeat_penalty,
            min_p=settings.ollama_min_p,
            presence_penalty=settings.ollama_presence_penalty,
            enable_thinking=settings.enable_thinking,
            preserve_thinking=settings.preserve_thinking_primary,
            reasoning_effort=settings.reasoning_effort_primary,
        )

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("LEAN_AI_OPENAI_API_KEY must be set when LEAN_AI_LLM_PROVIDER=openai")
        from lean_ai.llm.provider_openai import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_tokens=settings.openai_max_tokens,
            context_window=settings.openai_context_window,
            temperature=settings.openai_temperature,
            base_url=settings.openai_base_url or None,
            enable_thinking=settings.enable_thinking,
            preserve_thinking=settings.preserve_thinking_primary,
            reasoning_effort=settings.reasoning_effort_primary,
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
            reasoning_effort=settings.reasoning_effort_primary,
        )

    if provider == "serve":
        if not settings.serve_api_key:
            raise ValueError("LEAN_AI_SERVE_API_KEY must be set when LEAN_AI_LLM_PROVIDER=serve")
        if not settings.serve_model:
            raise ValueError("LEAN_AI_SERVE_MODEL must be set when LEAN_AI_LLM_PROVIDER=serve")
        from lean_ai.llm.provider_openai import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.serve_api_key,
            model=settings.serve_model,
            max_tokens=settings.serve_max_tokens,
            context_window=settings.serve_context_window,
            temperature=settings.serve_temperature,
            base_url=f"{settings.serve_url.rstrip('/')}/v1",
            enable_thinking=settings.enable_thinking,
            preserve_thinking=settings.preserve_thinking_primary,
            reasoning_effort=settings.reasoning_effort_primary,
        )

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("LEAN_AI_GEMINI_API_KEY must be set when LEAN_AI_LLM_PROVIDER=gemini")
        from lean_ai.llm.provider_gemini import GeminiProvider

        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            max_tokens=settings.gemini_max_tokens,
            context_window=settings.gemini_context_window,
            temperature=settings.gemini_temperature,
            enable_thinking=settings.enable_thinking,
            reasoning_effort=settings.reasoning_effort_primary,
        )

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. Supported: ollama, openai, anthropic, gemini, serve"
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
    LLMClient(
        provider=OllamaProvider(
            ollama_url=settings.effective_inline_url,
            model=settings.inline_model,
            max_tokens=settings.inline_max_tokens,
            context_window=settings.inline_context_window,
            temperature=None,
            top_p=None,
            top_k=None,
            repeat_penalty=None,
            min_p=None,
            presence_penalty=None,
            enable_thinking=False,
        )
    )
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
            top_p=settings.ollama_top_p,
            top_k=settings.ollama_top_k,
            repeat_penalty=settings.ollama_repeat_penalty,
            enable_thinking=settings.enable_thinking,
        )
        refiner = PromptRefiner(
            ollama_provider=_refiner_provider,
            enable_reference=settings.refiner_enable_reference,
            enable_privacy=settings.refiner_enable_privacy,
            reference_chunks=settings.refiner_reference_chunks,
            timeout=settings.refiner_timeout,
        )
        logger.info(
            "Local refiner enabled (model=%s, reference=%s, privacy=%s)",
            settings.effective_refiner_model,
            settings.refiner_enable_reference,
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
        ollama_min_p=settings.effective_expert_min_p,
        ollama_presence_penalty=settings.effective_expert_presence_penalty,
        openai_model=settings.openai_expert_model or "",
        anthropic_model=settings.anthropic_expert_model or "",
        gemini_model=settings.gemini_expert_model or "",
        serve_model=settings.serve_expert_model or "",
        preserve_thinking=settings.preserve_thinking_expert,
        reasoning_effort=settings.effective_expert_reasoning_effort,
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
        ollama_min_p=settings.effective_request_min_p,
        ollama_presence_penalty=settings.effective_request_presence_penalty,
        openai_model=settings.openai_request_model or "",
        anthropic_model=settings.anthropic_request_model or "",
        gemini_model=settings.gemini_request_model or "",
        serve_model=settings.serve_request_model or "",
        preserve_thinking=settings.preserve_thinking_request,
        reasoning_effort=settings.effective_request_reasoning_effort,
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
        ollama_min_p=settings.effective_worker_min_p,
        ollama_presence_penalty=settings.effective_worker_presence_penalty,
        openai_model=settings.openai_worker_model or "",
        anthropic_model=settings.anthropic_worker_model or "",
        gemini_model=settings.gemini_worker_model or "",
        serve_model=settings.serve_worker_model or "",
        preserve_thinking=settings.preserve_thinking_worker,
        reasoning_effort=settings.effective_worker_reasoning_effort,
        use_semaphore=True,
    ),
    semaphore=_llm_semaphore,
)


# ── Media capability resolvers ─────────────────────────────────────────

# When a user submits an image or audio, dispatch asks these helpers
# which client — if any — already has the capability flag set for its
# role.  Returning a flagged client lets us attach the media directly
# to that client's chat call (via ``llm.media_messages.attach_*``) and
# skip the round-trip through ``vision_model`` / faster-whisper, which
# on VRAM-constrained hosts would force a model swap.
#
# A return of ``("describe", vision_client)`` is the fallback signal for
# images — dispatch should run the legacy ``describe_image`` flow and
# inject text.  ``None`` means "feature is disabled; warn the user".
#
# For audio, ``None`` means "fall back to faster-whisper".

from typing import Literal  # noqa: E402


def _image_fallback_provider() -> "LLMClient | None":
    """Return a client wrapping ``vision_model`` (always Ollama), or None."""
    if not settings.vision_model:
        return None
    # Lightweight: the existing describe_image path uses its own Ollama
    # AsyncClient directly rather than our LLMClient facade, so callers
    # that opt into the describe branch use describe_image(); this helper
    # just signals the branch.  Return the primary llm_client as a
    # sentinel — callers check the mode, not the client, for describe.
    return llm_client


def resolve_image_handler(
    flow: Literal["chat", "workflow"] = "chat",
) -> tuple[str, "LLMClient"] | None:
    """Pick the client that should process an inbound image.

    Returns:
        ``("inline", client)`` — send image blocks to this client directly
        (it's vision-capable by declaration).
        ``("describe", client)`` — run the legacy ``describe_image`` flow
        against ``vision_model``; ``client`` is returned for completeness
        but the describe path doesn't use it.
        ``None`` — no image handler available; the caller should warn and
        drop the attachment.

    ``flow="chat"`` considers the request client first, then primary.
    ``flow="workflow"`` only considers primary (the role that sees user
    messages during plan Phase 1/2 and execution).
    """
    if flow == "chat":
        if request_llm_client is not None and settings.supports_image_request:
            return ("inline", request_llm_client)
        if settings.supports_image_primary:
            return ("inline", llm_client)
    elif flow == "workflow":
        if settings.supports_image_primary:
            return ("inline", llm_client)

    fallback = _image_fallback_provider()
    if fallback is not None:
        return ("describe", fallback)
    return None


def resolve_audio_handler() -> "LLMClient | None":
    """Pick the flagged client for audio transcription, else None.

    Priority order: primary → request → worker → expert → inline.
    Returning ``None`` signals the caller to use faster-whisper.
    ``CapabilityError`` raised later at dispatch time triggers the same
    Whisper fallback at the call site.
    """
    if settings.supports_audio_primary:
        return llm_client
    if settings.supports_audio_request and request_llm_client is not None:
        return request_llm_client
    if settings.supports_audio_worker and worker_llm_client is not None:
        return worker_llm_client
    if settings.supports_audio_expert and expert_llm_client is not None:
        return expert_llm_client
    if settings.supports_audio_inline and _inline_client is not None:
        return _inline_client
    return None
