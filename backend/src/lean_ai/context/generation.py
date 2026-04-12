"""LLM-based project context generation and post-processing.

Contains the public API (generate, write, update) and single-pass generation.
Multi-round expansion logic lives in ``expansion.py``; pure text processing
utilities live in ``text_processing.py``.
"""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import (
    _ADDITIVE_EXPANSION_PROMPT,
    _CONTEXT_GENERATION_SYSTEM_PROMPT,
    _MAX_FILE_CHARS,
    _scale_generation_caps,
)
from .content import (
    build_additive_expansion_prompt,
    build_generation_prompt,
)
from .expansion import (  # noqa: F401 — re-exported for backward compatibility
    _expand_project_context,
    _generate_project_context_multi_round,
    _merge_additions_into_doc,
)
from .text_processing import (  # noqa: F401 — re-exported for backward compatibility
    _EXPANSION_ARTIFACT_HEADINGS,
    _appears_truncated,
    _deduplicate_sections,
    _deduplicate_subsections,
    _normalize_h2,
    _truncate_repetition,
)

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


async def _generate_project_context_single_pass(
    repo_root: str,
    llm_client: "LLMClient",
    caps: dict[str, int],
    max_out: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Single-pass project context generation.

    Builds one large prompt from structural metadata + key files and
    calls the LLM once.  Suitable for context windows >= 64K.
    """
    user_prompt = build_generation_prompt(repo_root, section_caps=caps)

    messages = [
        {"role": "system", "content": _CONTEXT_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    content = await llm_client.chat_raw(
        messages=messages,
        max_tokens=max_out,
        thinking_callback=thinking_callback,
    )

    if _appears_truncated(content):
        logger.warning(
            "Single-pass context output appears truncated (%d chars). "
            "Consider increasing LEAN_AI_OLLAMA_CONTEXT_WINDOW or "
            "LEAN_AI_OLLAMA_MAX_TOKENS.",
            len(content),
        )

    return _truncate_repetition(content)


async def generate_project_context(
    repo_root: str,
    llm_client: "LLMClient",
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Generate a project context document using the LLM.

    Dispatches to single-pass or multi-round generation depending on
    context window size.
    """
    from lean_ai.config import settings

    logger.info("Generating project context for %s", repo_root)

    max_out = settings.ollama_max_tokens or settings.ollama_context_window // 4
    caps = _scale_generation_caps(settings.ollama_context_window, max_out)

    use_multi_round = (
        settings.enable_multi_round_context
        and settings.ollama_context_window < 65536
    )

    if use_multi_round:
        logger.info(
            "Using multi-round context generation (context_window=%d < 65536)",
            settings.ollama_context_window,
        )
        content = await _generate_project_context_multi_round(
            repo_root, llm_client, caps, max_out,
            context_window=settings.ollama_context_window,
            thinking_callback=thinking_callback,
        )
    else:
        logger.info(
            "Using single-pass context generation (context_window=%d)",
            settings.ollama_context_window,
        )
        content = await _generate_project_context_single_pass(
            repo_root, llm_client, caps, max_out,
            thinking_callback=thinking_callback,
        )

    content = _deduplicate_sections(content)
    content = _deduplicate_subsections(content)

    # ── Additive expansion: process remaining source files ──
    if settings.enable_multi_round_context:
        try:
            content = await _expand_project_context(
                content, repo_root, llm_client, caps, max_out,
                context_window=settings.ollama_context_window,
                thinking_callback=thinking_callback,
            )
        except Exception as exc:
            logger.warning("Additive expansion failed (non-fatal): %s", exc)

    # ── Mechanical section reorganization (merge same headings, drop
    #    exact-match lines) ──
    from lean_ai.context.dedup import reorganize_sections

    content = reorganize_sections(content, log_prefix="Project context")

    logger.info("Project context generated: %d chars", len(content))
    return content


def write_project_context(repo_root: str, content: str) -> str:
    """Write project context to ``.lean_ai/project_context.md``."""
    output_dir = Path(repo_root) / ".lean_ai"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "project_context.md"
    output_path.write_text(content, encoding="utf-8")

    logger.info("Project context written to %s", output_path)
    return str(output_path)


async def update_project_context(
    repo_root: str,
    modified_paths: list[str],
    llm_client: "LLMClient",
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str | None:
    """Incrementally update project_context.md with changes from modified files.

    Reads the current document, feeds the modified files through one additive
    expansion round, merges the result, and writes back.  Returns the output
    path, or ``None`` if no update was possible.

    Non-fatal -- logs warnings on failure and returns ``None``.
    """
    from lean_ai.config import settings

    ctx_path = Path(repo_root) / ".lean_ai" / "project_context.md"
    if not ctx_path.is_file():
        logger.info("update_project_context: no existing context file, skipping")
        return None

    existing_doc = ctx_path.read_text(encoding="utf-8")
    if not existing_doc.strip():
        return None

    # Read modified files into a batch.
    max_out = settings.ollama_max_tokens or settings.ollama_context_window // 4
    caps = _scale_generation_caps(settings.ollama_context_window, max_out)
    max_file = caps.get("max_file_chars", _MAX_FILE_CHARS)

    root = Path(repo_root)
    parts: list[str] = []
    for rel_path in modified_paths:
        full = root / rel_path
        if not full.is_file():
            continue
        try:
            content = full.read_text(encoding="utf-8")[:max_file]
        except (UnicodeDecodeError, OSError):
            continue
        parts.append(f"--- {rel_path} ---\n```\n{content}\n```")

    if not parts:
        logger.info("update_project_context: no readable modified files, skipping")
        return None

    batch = "\n\n".join(parts)
    logger.info(
        "update_project_context: updating with %d modified file(s) (%d chars)",
        len(parts), len(batch),
    )

    user_msg = build_additive_expansion_prompt(existing_doc, batch)
    messages = [
        {"role": "system", "content": _ADDITIVE_EXPANSION_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    additions = await llm_client.chat_raw(
        messages=messages,
        max_tokens=max_out,
        thinking_callback=thinking_callback,
    )
    additions = _truncate_repetition(additions)

    if not additions.strip():
        logger.info("update_project_context: LLM produced empty output, skipping")
        return None

    # The LLM returns the complete updated document.
    # Sanity check: output should be at least 70% of current length.
    if len(additions) >= len(existing_doc) * 0.7:
        merged = additions
    else:
        logger.warning(
            "update_project_context: output too short (%d vs %d chars), "
            "keeping original",
            len(additions), len(existing_doc),
        )
        merged = existing_doc

    path = write_project_context(repo_root, merged)
    logger.info(
        "update_project_context: done (%d -> %d chars)",
        len(existing_doc), len(merged),
    )
    return path
