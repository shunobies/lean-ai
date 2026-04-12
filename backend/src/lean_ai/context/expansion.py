"""Multi-round and additive expansion logic for project context generation.

Contains the sequential multi-round strategy (for small context windows),
the parallel additive expansion strategy, and the merge utility that
combines additions-only outputs into the base document.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .constants import (
    _EXPANSION_SYSTEM_PROMPT,
    _MAX_DOC_FILE_CHARS,
    _MAX_FILE_CHARS,
    _PARALLEL_EXPANSION_PROMPT,
)
from .content import (
    _batch_file_contents,
    _build_expansion_prompt,
    _collect_all_ranked_candidates,
    _collect_priority_file_contents,
    build_parallel_expansion_prompt,
    extract_section_headings,
)
from .metadata import extract_metadata_cached
from .text_processing import _appears_truncated, _truncate_repetition

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


def _merge_additions_into_doc(base_doc: str, additions_list: list[str]) -> str:
    """Merge multiple additions-only outputs into the base document.

    Each additions output contains ``## `` headings with new content.
    For each heading found in the additions, the new content is appended
    at the end of the corresponding section in the base document (just
    before the next ``## `` heading, or at the document end).

    Handles:
    - Multiple additions referencing the same heading (all get appended).
    - Headings in additions that don't exist in base (logged, skipped).
    - Empty additions (skipped).
    """
    if not additions_list:
        return base_doc

    # Parse additions into heading -> [content_blocks].
    heading_additions: dict[str, list[str]] = defaultdict(list)
    for additions_text in additions_list:
        if not additions_text.strip():
            continue
        current_heading = ""
        current_lines: list[str] = []
        for line in additions_text.split("\n"):
            if line.strip().startswith("## "):
                if current_heading and current_lines:
                    content = "\n".join(current_lines).strip()
                    if content:
                        heading_additions[current_heading].append(content)
                current_heading = line.strip()
                current_lines = []
            elif current_heading:
                current_lines.append(line)
        # Flush last section.
        if current_heading and current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                heading_additions[current_heading].append(content)

    if not heading_additions:
        return base_doc

    # Parse base document into sections: (heading, start_line, end_line).
    lines = base_doc.split("\n")
    sections: list[tuple[str, int, int]] = []
    for i, line in enumerate(lines):
        if line.strip().startswith("## "):
            sections.append((line.strip(), i, -1))
            if len(sections) > 1:
                sections[-2] = (sections[-2][0], sections[-2][1], i)
    if sections:
        sections[-1] = (sections[-1][0], sections[-1][1], len(lines))

    # Build heading -> section end line mapping.
    heading_end: dict[str, int] = {}
    for heading, _start, end in sections:
        heading_end[heading] = end

    # Insert additions at the end of each matching section.
    # Process in reverse document order so line indices stay valid.
    for heading in reversed(list(heading_end.keys())):
        if heading not in heading_additions:
            continue
        end_line = heading_end[heading]
        # Strip trailing blank lines from the section.
        while end_line > 0 and not lines[end_line - 1].strip():
            end_line -= 1
        combined = "\n\n".join(heading_additions[heading])
        insert_lines = ["", combined, ""]
        lines[end_line:end_line] = insert_lines

    # Log any unknown headings.
    for heading in heading_additions:
        if heading not in heading_end:
            logger.info(
                "merge: heading %r not in base document, skipping", heading,
            )

    return "\n".join(lines)


async def _generate_project_context_multi_round(
    repo_root: str,
    llm_client: "LLMClient",
    caps: dict[str, int],
    max_out: int,
    context_window: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Multi-round project context generation for small context windows.

    Round 1: structural metadata + priority files.
    Rounds 2+: expand with additional file batches.
    Each round starts with a fresh context window.
    """
    from lean_ai.indexer.tree import list_repo_tree

    from .constants import _CONTEXT_GENERATION_SYSTEM_PROMPT
    from .content import build_generation_prompt

    try:
        entries = list_repo_tree(repo_root)
    except Exception:
        logger.warning("multi-round: could not list repo tree, falling back to single-pass")
        from .generation import _generate_project_context_single_pass

        return await _generate_project_context_single_pass(
            repo_root, llm_client, caps, max_out,
        )

    metadata = extract_metadata_cached(repo_root, entries=entries)

    # ── Round 1: standard generation prompt ──
    logger.info("multi-round context: round 1 (initial generation)")
    user_prompt = build_generation_prompt(repo_root, section_caps=caps)

    round1_messages = [
        {"role": "system", "content": _CONTEXT_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    current_doc = await llm_client.chat_raw(
        messages=round1_messages,
        max_tokens=max_out,
        thinking_callback=thinking_callback,
    )
    current_doc = _truncate_repetition(current_doc)
    logger.info("multi-round context: round 1 complete (%d chars)", len(current_doc))

    # ── Identify files already covered in round 1 ──
    _priority_content, priority_paths = _collect_priority_file_contents(
        repo_root,
        entries=entries,
        max_file_chars=caps.get("max_file_chars", _MAX_FILE_CHARS),
        max_doc_file_chars=caps.get("max_doc_file_chars", _MAX_DOC_FILE_CHARS),
    )

    remaining = _collect_all_ranked_candidates(
        repo_root,
        entries=entries,
        fan_in=metadata.fan_in,
        exclude_paths=priority_paths,
        max_file_chars=caps.get("max_file_chars", _MAX_FILE_CHARS),
    )

    if not remaining:
        logger.info("multi-round context: no remaining files for expansion rounds")
        return current_doc

    # ── Budget for expansion rounds ──
    input_budget_chars = (context_window - max_out) * 4
    batch_budget_chars = max(4000, input_budget_chars // 2)

    batches = _batch_file_contents(remaining, batch_budget_chars)

    if not batches:
        return current_doc

    total_rounds = 1 + len(batches)
    logger.info(
        "multi-round context: %d expansion round(s) planned "
        "(%d remaining files, batch_budget=%d chars)",
        len(batches), len(remaining), batch_budget_chars,
    )

    # ── Rounds 2+: expansion ──
    for i, batch_content in enumerate(batches):
        round_num = i + 2
        logger.info(
            "multi-round context: round %d of %d (%d chars of new files)",
            round_num, total_rounds, len(batch_content),
        )

        user_msg = _build_expansion_prompt(
            current_doc, batch_content, round_num, total_rounds,
        )
        expansion_messages = [
            {"role": "system", "content": _EXPANSION_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        updated_doc = await llm_client.chat_raw(
            messages=expansion_messages,
            max_tokens=max_out,
            thinking_callback=thinking_callback,
        )
        updated_doc = _truncate_repetition(updated_doc)

        if _appears_truncated(updated_doc):
            logger.warning(
                "Multi-round context output appears truncated (round %d, %d chars).",
                round_num, len(updated_doc),
            )

        if len(updated_doc) >= len(current_doc) // 2:
            current_doc = updated_doc
            logger.info(
                "multi-round context: round %d complete (%d chars)",
                round_num, len(current_doc),
            )
        else:
            logger.warning(
                "multi-round context: round %d produced short output "
                "(%d chars vs previous %d chars) — keeping previous",
                round_num, len(updated_doc), len(current_doc),
            )

    return current_doc


async def _expand_project_context(
    base_doc: str,
    repo_root: str,
    llm_client: "LLMClient",
    caps: dict[str, int],
    max_out: int,
    context_window: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Additive expansion: process remaining source files and merge findings.

    All batches fire concurrently via ``asyncio.gather``.  Concurrency is
    controlled by the shared semaphore inside ``LLMClient.chat_raw`` (set
    via the ``LEAN_AI_NUM_PARALLEL`` / ``num_parallel`` setting), so no
    local semaphore is needed.
    """
    from lean_ai.indexer.tree import list_repo_tree

    try:
        entries = list_repo_tree(repo_root)
    except Exception:
        logger.warning("expansion: could not list repo tree, skipping")
        return base_doc

    metadata = extract_metadata_cached(repo_root, entries=entries)

    # Identify files already covered in round 1.
    _priority_content, priority_paths = _collect_priority_file_contents(
        repo_root,
        entries=entries,
        max_file_chars=caps.get("max_file_chars", _MAX_FILE_CHARS),
        max_doc_file_chars=caps.get("max_doc_file_chars", _MAX_DOC_FILE_CHARS),
    )

    remaining = _collect_all_ranked_candidates(
        repo_root,
        entries=entries,
        fan_in=metadata.fan_in,
        exclude_paths=priority_paths,
        max_file_chars=caps.get("max_file_chars", _MAX_FILE_CHARS),
    )

    if not remaining:
        logger.info("expansion: no remaining files, skipping")
        return base_doc

    # Budget per batch: half of available input chars (leaves room for
    # section headings + covered names in the prompt).
    input_budget_chars = (context_window - max_out) * 4
    batch_budget_chars = max(4000, input_budget_chars // 2)

    batches = _batch_file_contents(remaining, batch_budget_chars)
    if not batches:
        return base_doc

    max_rounds = 20  # Safety cap for enormous repos
    batches = batches[:max_rounds]

    logger.info(
        "expansion: %d batch(es) planned (%d remaining files, "
        "batch_budget=%d chars)",
        len(batches), len(remaining), batch_budget_chars,
    )

    # ── Fire all batches (semaphore in chat_raw handles throttling) ──
    section_headings = extract_section_headings(base_doc)

    async def _process_batch(batch_idx: int, batch_content: str) -> str:
        round_num = batch_idx + 2
        logger.info(
            "expansion: batch %d (%d chars of new files)",
            round_num, len(batch_content),
        )
        user_msg = build_parallel_expansion_prompt(
            section_headings, batch_content,
        )
        messages = [
            {"role": "system", "content": _PARALLEL_EXPANSION_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        try:
            additions = await llm_client.chat_raw(
                messages=messages,
                max_tokens=max_out,
                thinking_callback=thinking_callback,
            )
            additions = _truncate_repetition(additions)
            logger.info(
                "expansion: batch %d complete (%d chars)",
                round_num, len(additions),
            )
            return additions
        except Exception as exc:
            logger.warning(
                "expansion: batch %d failed (non-fatal): %s",
                round_num, exc,
            )
            return ""

    tasks = [
        _process_batch(i, batch_content)
        for i, batch_content in enumerate(batches)
    ]
    results = await asyncio.gather(*tasks)

    # Filter empty results.
    valid_additions = [r for r in results if r.strip()]
    if not valid_additions:
        logger.warning("expansion: all batches returned empty")
        return base_doc

    # Merge all additions into base document.
    merged = _merge_additions_into_doc(base_doc, valid_additions)
    logger.info(
        "expansion: merged %d batch result(s) (%d -> %d chars)",
        len(valid_additions), len(base_doc), len(merged),
    )

    return merged
