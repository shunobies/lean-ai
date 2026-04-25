"""Model listing, health check, and inline prediction endpoints."""

import logging

from fastapi import APIRouter

from lean_ai.config import settings
from lean_ai.routers.dependencies import _inline_client
from lean_ai.routers.models import (
    InlinePredictRequest,
    ModelInfo,
    ModelsResponse,
)

logger = logging.getLogger(__name__)

info_router = APIRouter()


def _get_default_model_name() -> str:
    """Return the model name for the active provider."""
    p = settings.llm_provider.lower()
    if p == "openai":
        return settings.openai_model
    if p == "anthropic":
        return settings.anthropic_model
    if p == "gemini":
        return settings.gemini_model
    if p == "serve":
        return settings.serve_model
    return settings.ollama_model


def _get_active_max_tokens() -> int:
    """Return max_tokens for the active provider."""
    p = settings.llm_provider.lower()
    if p == "openai":
        return settings.openai_max_tokens
    if p == "anthropic":
        return settings.anthropic_max_tokens
    if p == "gemini":
        return settings.gemini_max_tokens
    if p == "serve":
        return settings.serve_max_tokens
    return settings.ollama_max_tokens


@info_router.get("/models", response_model=ModelsResponse)
async def list_models():
    """List available LLM providers/models based on server configuration."""
    models: list[ModelInfo] = []

    # Ollama: query live API for available models
    try:
        import ollama as ollama_lib

        client = ollama_lib.AsyncClient(host=settings.ollama_url)
        response = await client.list()
        for m in response.models:
            name = m.model
            models.append(
                ModelInfo(
                    provider="ollama",
                    model=name,
                    display_name=f"Ollama: {name}",
                    is_default=(
                        settings.llm_provider == "ollama" and name == settings.ollama_model
                    ),
                )
            )
    except Exception:
        # Ollama not reachable — add the configured model as fallback
        models.append(
            ModelInfo(
                provider="ollama",
                model=settings.ollama_model,
                display_name=f"Ollama: {settings.ollama_model}",
                is_default=settings.llm_provider == "ollama",
            )
        )

    # OpenAI: show if API key configured
    if settings.openai_api_key:
        models.append(
            ModelInfo(
                provider="openai",
                model=settings.openai_model,
                display_name=f"OpenAI: {settings.openai_model}",
                is_default=settings.llm_provider == "openai",
            )
        )

    # Anthropic: show if API key configured
    if settings.anthropic_api_key:
        models.append(
            ModelInfo(
                provider="anthropic",
                model=settings.anthropic_model,
                display_name=f"Anthropic: {settings.anthropic_model}",
                is_default=settings.llm_provider == "anthropic",
            )
        )

    # Gemini: show if API key configured
    if settings.gemini_api_key:
        models.append(
            ModelInfo(
                provider="gemini",
                model=settings.gemini_model,
                display_name=f"Gemini: {settings.gemini_model}",
                is_default=settings.llm_provider == "gemini",
            )
        )

    # Lean AI Serve: show if API key and model configured
    if settings.serve_api_key and settings.serve_model:
        models.append(
            ModelInfo(
                provider="serve",
                model=settings.serve_model,
                display_name=f"Serve: {settings.serve_model}",
                is_default=settings.llm_provider == "serve",
            )
        )

    return ModelsResponse(
        models=models,
        default_provider=settings.llm_provider,
        default_model=_get_default_model_name(),
    )


@info_router.post("/predict")
async def inline_predict(request: InlinePredictRequest):
    """Stateless inline prediction — Copilot-style completions."""
    try:
        completion = await _inline_client.generate_completion(
            request.prefix,
            suffix=request.suffix,
        )
        confidence = 0.8 if completion.strip() else 0.0
        return {"completion": completion, "confidence": confidence}
    except Exception as e:
        logger.exception("Inline prediction failed")
        return {"completion": "", "confidence": 0.0, "error": str(e)}


@info_router.get("/health")
async def health():
    """Health check.

    The ``busy`` field lists any long-running tasks currently in flight
    (e.g. ``embeddings.code`` during ``/init``). Extension-side health
    monitors should use it to skip automatic restarts when a probe
    response comes back slow — slow while ``busy`` is non-empty is
    expected, not a failure.
    """
    from lean_ai.llm.vision import is_vision_available
    from lean_ai.runtime_state import current_busy
    from lean_ai.tools.ui_analysis import is_analysis_available

    result = {
        "status": "ok",
        "vision_available": is_vision_available(),
        "ui_verification_available": is_analysis_available(),
        "busy": current_busy(),
    }
    try:
        from lean_ai.voice.availability import voice_status

        result.update(voice_status())
    except ImportError:
        result.update(
            {
                "stt_available": False,
                "tts_available": False,
                "wake_word_available": False,
            }
        )
    return result
