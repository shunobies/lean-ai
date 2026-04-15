"""LLM-based project context generation and post-processing.

Contains the public API (generate, write, update).  Pure text processing
utilities live in ``text_processing.py``.

The primary generation approach is **iterative file-by-file**: a skeleton is
produced from structural metadata, then each source file is processed
individually in a read-modify-write loop with progress feedback and
intermediate disk writes.
"""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import (
    _ADDITIVE_EXPANSION_PROMPT,
    _ITERATIVE_INPUT_BUDGET_CHARS,
    _MAX_FILE_CHARS,
    _SINGLE_FILE_UPDATE_PROMPT,
    _SKELETON_GENERATION_SYSTEM_PROMPT,
    _scale_generation_caps,
)
from .content import (
    _collect_all_ranked_candidates,
    build_additive_expansion_prompt,
    build_single_file_headings_prompt,
    build_single_file_update_prompt,
    build_skeleton_generation_prompt,
    extract_section_headings,
)
from .expansion import _merge_additions_into_doc
from .text_processing import (
    _deduplicate_sections,
    _deduplicate_subsections,
    _truncate_repetition,
)

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


ProgressCallback = Callable[[dict], Awaitable[None]]


async def _emit_progress(
    callback: ProgressCallback | None, **kwargs: object,
) -> None:
    """Fire a progress event if *callback* is set."""
    if callback:
        await callback(kwargs)


async def generate_project_context(
    repo_root: str,
    llm_client: "LLMClient",
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Generate a project context document using iterative file-by-file passes.

    **Phase 1 — Skeleton:** Produce a structural overview from metadata
    (file tree, class/function index, import graph, API endpoints) with
    no source file contents.  Written to disk immediately.

    **Phase 2 — File-by-file:** For each source file (ranked by fan-in),
    read the current document + the single file, ask the LLM to update the
    document, validate the output, and write to disk.  Emits per-file
    progress events.

    When the document grows too large for the context budget the loop
    switches to *headings-only mode*: only section headings are sent
    instead of the full document, and the LLM's additions are merged back
    programmatically.
    """
    from lean_ai.config import settings

    logger.info("Generating project context for %s", repo_root)

    max_out = (
        settings.ollama_max_tokens
        or settings.ollama_context_window // 4
    )
    caps = _scale_generation_caps(settings.ollama_context_window, max_out)
    max_file = caps.get("max_file_chars", _MAX_FILE_CHARS)

    # ── Phase 1: skeleton from structural metadata ──────────────────
    await _emit_progress(
        progress_callback,
        phase="skeleton",
        message="Generating structural skeleton...",
        current=0,
        total=0,
        chars=0,
    )

    skeleton_prompt = build_skeleton_generation_prompt(
        repo_root, section_caps=caps,
    )
    messages = [
        {"role": "system", "content": _SKELETON_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": skeleton_prompt},
    ]

    content = await llm_client.chat_raw(
        messages=messages,
        max_tokens=max_out,
        thinking_callback=thinking_callback,
    )
    content = _truncate_repetition(content)

    if not content.strip():
        logger.warning("Skeleton generation produced empty output")
        content = "# Project Context\n\n(skeleton generation failed)\n"

    # Checkpoint: write skeleton to disk immediately.
    write_project_context(repo_root, content)
    logger.info(
        "Phase 1 (skeleton) complete: %d chars", len(content),
    )

    # ── Phase 2: one file per round ─────────────────────────────────
    try:
        from lean_ai.indexer.tree import list_repo_tree
        entries = list_repo_tree(repo_root)
    except Exception:
        entries = []

    from .metadata import extract_metadata_cached
    metadata = extract_metadata_cached(repo_root, entries=entries)

    # Collect all source files ranked by fan-in (most-imported first).
    # The skeleton already covered structural metadata; priority docs /
    # entry points were visible in the index.  We process ALL ranked
    # source files here so the document gains real implementation detail.
    candidates = _collect_all_ranked_candidates(
        repo_root,
        entries=entries,
        fan_in=metadata.fan_in,
        exclude_paths=set(),  # process everything
        max_file_chars=max_file,
    )

    total_files = len(candidates)
    logger.info(
        "Phase 2: %d source files to process one-by-one", total_files,
    )

    system_prompt_chars = len(_SINGLE_FILE_UPDATE_PROMPT)

    for idx, (file_path, file_content) in enumerate(candidates):
        file_num = idx + 1

        # ── Context budget check ──
        doc_chars = len(content)
        total_input = system_prompt_chars + doc_chars + len(file_content)
        use_headings_only = (
            total_input > _ITERATIVE_INPUT_BUDGET_CHARS
        )

        mode_label = " (headings-only)" if use_headings_only else ""
        await _emit_progress(
            progress_callback,
            phase="file_update",
            message=(
                f"Processing file {file_num} of {total_files}: "
                f"{file_path}{mode_label}"
            ),
            current=idx,
            total=total_files,
            chars=doc_chars,
            headings_only=use_headings_only,
        )
        logger.info(
            "File %d/%d%s: %s (%d chars)",
            file_num, total_files, mode_label,
            file_path, len(file_content),
        )

        try:
            headings = extract_section_headings(content)

            if use_headings_only:
                # Headings-only: send headings + file (no document text).
                user_msg = build_single_file_headings_prompt(
                    headings, file_path, file_content,
                )
            else:
                # Full mode: send headings + document text + file.
                user_msg = build_single_file_update_prompt(
                    content, headings, file_path, file_content,
                )

            msgs = [
                {"role": "system", "content": _SINGLE_FILE_UPDATE_PROMPT},
                {"role": "user", "content": user_msg},
            ]
            additions = await llm_client.chat_raw(
                messages=msgs,
                max_tokens=max_out,
                thinking_callback=thinking_callback,
            )
            additions = _truncate_repetition(additions)

            if (
                additions.strip()
                and "NO_NEW_INFO" not in additions
            ):
                content = _merge_additions_into_doc(
                    content, [additions],
                )
        except Exception as exc:
            logger.warning(
                "File %d/%d (%s) failed (non-fatal): %s",
                file_num, total_files, file_path, exc,
            )

        # Checkpoint: write after every file.
        write_project_context(repo_root, content)

    # ── Cleanup ─────────────────────────────────────────────────────
    await _emit_progress(
        progress_callback,
        phase="cleanup",
        message="Cleaning up...",
        current=total_files,
        total=total_files,
        chars=len(content),
    )

    content = _deduplicate_sections(content)
    content = _deduplicate_subsections(content)

    from lean_ai.context.dedup import reorganize_sections

    content = reorganize_sections(
        content, log_prefix="Project context",
    )

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
