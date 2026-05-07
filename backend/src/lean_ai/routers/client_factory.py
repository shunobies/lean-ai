"""Generic factory for creating role-specific LLM clients (expert, request, worker).

Eliminates the repeated provider if/elif chains that were duplicated per role
in dependencies.py.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lean_ai.config import settings
from lean_ai.llm.client import OllamaProvider
from lean_ai.llm.facade import LLMClient

if TYPE_CHECKING:
    from lean_ai.llm.base import LLMProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoleConfig:
    """Provider-agnostic configuration for a model role."""

    role_name: str
    provider_setting: str  # e.g. "expert_llm_provider"
    enable_thinking: bool

    # Ollama-specific
    ollama_model: str  # e.g. settings.ollama_model_expert
    ollama_max_tokens: int
    ollama_context_window: int
    ollama_temperature: float | None = None
    ollama_top_p: float | None = None
    ollama_top_k: int | None = None
    ollama_repeat_penalty: float | None = None
    # Optional sampling params — None means "omit from options dict" so
    # models that don't support them aren't confused.
    ollama_min_p: float | None = None
    ollama_presence_penalty: float | None = None

    # OpenAI
    openai_model: str = ""
    # Anthropic
    anthropic_model: str = ""
    # Gemini
    gemini_model: str = ""
    # Serve
    serve_model: str = ""

    # Qwen3.6 / vLLM chat_template_kwargs — keeps the model's prior-turn
    # chain-of-thought in the rendered prompt so tool loops don't re-derive
    # the same reasoning each iteration.
    preserve_thinking: bool = False
    # Per-role soft cap on thinking tokens.  Ollama enforces via a
    # client-side stream interrupt; cloud providers forward the setting
    # to their native reasoning-budget parameter.
    reasoning_effort: str = ""

    use_semaphore: bool = False


def _create_openai_provider(
    model: str,
    enable_thinking: bool,
    preserve_thinking: bool = False,
    reasoning_effort: str = "",
) -> LLMProvider:
    from lean_ai.llm.provider_openai import OpenAIProvider

    return OpenAIProvider(
        api_key=settings.openai_api_key,
        model=model,
        base_url=settings.openai_base_url or None,
        temperature=settings.openai_temperature,
        context_window=settings.openai_context_window,
        max_tokens=settings.openai_max_tokens,
        enable_thinking=enable_thinking,
        preserve_thinking=preserve_thinking,
        reasoning_effort=reasoning_effort,
    )


def _create_anthropic_provider(
    model: str,
    enable_thinking: bool,
    preserve_thinking: bool = False,
    reasoning_effort: str = "",
) -> LLMProvider:
    # Anthropic doesn't honor chat_template_kwargs — preserve_thinking is a
    # no-op here.  Accept the arg for interface parity with the other
    # factories so the caller can flag it uniformly.
    _ = preserve_thinking
    from lean_ai.llm.provider_anthropic import AnthropicProvider

    return AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=model,
        temperature=settings.anthropic_temperature,
        context_window=settings.anthropic_context_window,
        max_tokens=settings.anthropic_max_tokens,
        enable_thinking=enable_thinking,
        reasoning_effort=reasoning_effort,
    )


def _create_serve_provider(
    model: str,
    enable_thinking: bool,
    preserve_thinking: bool = False,
    reasoning_effort: str = "",
) -> LLMProvider:
    from lean_ai.llm.provider_openai import OpenAIProvider

    return OpenAIProvider(
        api_key=settings.serve_api_key,
        model=model,
        base_url=f"{settings.serve_url.rstrip('/')}/v1",
        temperature=settings.serve_temperature,
        context_window=settings.serve_context_window,
        max_tokens=settings.serve_max_tokens,
        enable_thinking=enable_thinking,
        preserve_thinking=preserve_thinking,
        reasoning_effort=reasoning_effort,
    )


def _create_gemini_provider(
    model: str,
    enable_thinking: bool,
    preserve_thinking: bool = False,
    reasoning_effort: str = "",
) -> LLMProvider:
    # Gemini has its own thinking-preservation semantics; preserve_thinking
    # is a no-op here.  Accept for interface parity.
    _ = preserve_thinking
    from lean_ai.llm.provider_gemini import GeminiProvider

    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model=model,
        temperature=settings.gemini_temperature,
        context_window=settings.gemini_context_window,
        max_tokens=settings.gemini_max_tokens,
        enable_thinking=enable_thinking,
        reasoning_effort=reasoning_effort,
    )


# Maps provider name -> (required_key_attr, model_resolver, factory)
_CLOUD_PROVIDERS: dict[str, tuple[str, str, object]] = {}  # built lazily


def _resolve_model(cfg: RoleConfig, provider: str) -> str:
    """Return the role-specific model name, falling back to the default for that provider."""
    if provider == "openai":
        return cfg.openai_model or settings.openai_model
    if provider == "anthropic":
        return cfg.anthropic_model or settings.anthropic_model
    if provider == "serve":
        return cfg.serve_model or settings.serve_model
    if provider == "gemini":
        return cfg.gemini_model or settings.gemini_model
    return ""


def _required_key(provider: str) -> tuple[str, str | None]:
    """Return (key_name_for_logging, actual_key_value) for a cloud provider."""
    if provider == "openai":
        return "OPENAI_API_KEY", settings.openai_api_key
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY", settings.anthropic_api_key
    if provider == "serve":
        return "SERVE_API_KEY", settings.serve_api_key
    if provider == "gemini":
        return "GEMINI_API_KEY", settings.gemini_api_key
    return "", None


def _factory_for(provider: str):
    """Return the provider factory function."""
    return {
        "openai": _create_openai_provider,
        "anthropic": _create_anthropic_provider,
        "serve": _create_serve_provider,
        "gemini": _create_gemini_provider,
    }.get(provider)


def create_role_client(
    cfg: RoleConfig,
    semaphore: asyncio.Semaphore | None = None,
) -> LLMClient | None:
    """Create an LLM client for a named role (expert, request, worker).

    Returns None if the role is not configured or creation fails.
    """
    provider = (cfg.provider_setting or "").lower()

    # Auto-detect: Ollama role only when primary is also Ollama
    if not provider:
        if settings.llm_provider.lower() == "ollama" and cfg.ollama_model:
            provider = "ollama"
        else:
            return None

    sem = semaphore if cfg.use_semaphore else None

    # -- Cloud providers (openai, anthropic, serve, gemini) --
    factory = _factory_for(provider)
    if factory is not None:
        model = _resolve_model(cfg, provider)
        key_name, key_val = _required_key(provider)

        if not model or not key_val:
            logger.warning(
                "%s provider is %r but %s — %s model disabled",
                cfg.role_name,
                provider,
                f"{key_name} is not set" if not key_val else "no model name resolved",
                cfg.role_name.lower(),
            )
            return None

        try:
            role_provider = factory(
                model,
                cfg.enable_thinking,
                cfg.preserve_thinking,
                cfg.reasoning_effort,
            )
            client = LLMClient(provider=role_provider, concurrency_semaphore=sem)
            logger.info(
                "%s model enabled (%s): %s",
                cfg.role_name,
                provider.capitalize(),
                model,
            )
            return client
        except Exception as e:
            logger.warning(
                "Could not create %s LLM client (%s): %s",
                cfg.role_name.lower(),
                provider.capitalize(),
                e,
            )
            return None

    # -- Ollama --
    if provider == "ollama" and cfg.ollama_model:
        try:
            ollama_kw: dict = {
                "ollama_url": settings.ollama_url,
                "model": cfg.ollama_model,
                "max_tokens": cfg.ollama_max_tokens,
                "context_window": cfg.ollama_context_window or settings.ollama_context_window,
                "temperature": cfg.ollama_temperature,
                "top_p": cfg.ollama_top_p,
                "top_k": cfg.ollama_top_k,
                "repeat_penalty": cfg.ollama_repeat_penalty,
                "min_p": cfg.ollama_min_p,
                "presence_penalty": cfg.ollama_presence_penalty,
                "enable_thinking": cfg.enable_thinking,
                "preserve_thinking": cfg.preserve_thinking,
            }
            if cfg.reasoning_effort:
                ollama_kw["reasoning_effort"] = cfg.reasoning_effort

            role_provider = OllamaProvider(**ollama_kw)
            client = LLMClient(provider=role_provider, concurrency_semaphore=sem)
            logger.info(
                "%s model enabled (Ollama): %s (ctx=%s, max_tokens=%s)",
                cfg.role_name,
                cfg.ollama_model,
                cfg.ollama_context_window or settings.ollama_context_window,
                cfg.ollama_max_tokens,
            )
            return client
        except Exception as e:
            logger.warning(
                "Could not create %s LLM client (Ollama): %s",
                cfg.role_name.lower(),
                e,
            )
            return None

    return None
