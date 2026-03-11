"""Shared singletons for all router sub-modules."""

from lean_ai.config import settings
from lean_ai.llm.client import LLMClient

llm_client = LLMClient()

_inline_client: LLMClient = (
    LLMClient(
        ollama_url=settings.effective_inline_url,
        model=settings.inline_model,
        max_tokens=settings.inline_max_tokens,
        context_window=settings.inline_context_window,
    )
    if settings.inline_model
    else llm_client
)
