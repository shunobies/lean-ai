"""LLM-based project context generation and post-processing.

Contains the single-pass and multi-round generation strategies, as well as
repetition detection, section deduplication, and file output.

No regex — all text processing uses simple string operations.
"""

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import (
    _ADDITIVE_EXPANSION_PROMPT,
    _CONTEXT_GENERATION_SYSTEM_PROMPT,
    _EXPANSION_SYSTEM_PROMPT,
    _MAX_DOC_FILE_CHARS,
    _MAX_FILE_CHARS,
    _PARALLEL_EXPANSION_PROMPT,
    _scale_generation_caps,
)
from .content import (
    _batch_file_contents,
    _build_expansion_prompt,
    _collect_all_ranked_candidates,
    _collect_priority_file_contents,
    build_additive_expansion_prompt,
    build_generation_prompt,
    build_parallel_expansion_prompt,
    extract_section_headings,
)
from .metadata import extract_metadata_cached

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


def _truncate_repetition(text: str, *, max_repeats: int = 5) -> str:
    """Detect and truncate degenerate repetition in LLM output.

    Handles both line-level repetition (same line repeated) and
    intra-line repetition (same phrase repeated on a single line).

    No regex — uses simple string comparison.
    """
    # ── Line-level repetition ──
    out_lines: list[str] = []
    prev_line = None
    repeat_count = 0

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == prev_line and stripped:
            repeat_count += 1
            if repeat_count <= max_repeats:
                out_lines.append(line)
            elif repeat_count == max_repeats + 1:
                out_lines.append("... (repetition truncated)")
        else:
            out_lines.append(line)
            prev_line = stripped
            repeat_count = 1

    result = "\n".join(out_lines)

    # ── Intra-line repetition ──
    # Look for repeated substrings within long lines.
    def _truncate_inline(line: str) -> str:
        if len(line) < 500:
            return line
        # Search for repeated phrases of length 15-80 chars
        for phrase_len in range(15, 80):
            for start in range(0, min(len(line) - phrase_len * 3, 500)):
                phrase = line[start:start + phrase_len]
                if not phrase.strip():
                    continue
                count = 0
                pos = start
                while pos <= len(line) - phrase_len:
                    if line[pos:pos + phrase_len] == phrase:
                        count += 1
                        pos += phrase_len
                    else:
                        break
                if count > max_repeats:
                    kept = phrase * max_repeats
                    return line[:start] + kept + " ... (repetition truncated)"
        return line

    final_lines = []
    for line in result.split("\n"):
        final_lines.append(_truncate_inline(line))
    return "\n".join(final_lines)


def _appears_truncated(text: str) -> bool:
    """Check if text appears to be truncated by a token limit.

    Heuristic: if the text does not end with a complete line
    (newline, period, or Markdown closing), it was likely cut off.

    No regex — checks the last non-whitespace character.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    last_char = stripped[-1]
    # Normal endings: newline, period, backtick (code block end),
    # dash (list item end), closing paren/bracket
    return last_char not in ("\n", ".", "`", "-", ")", "]", "}")


# Section headings that expansion rounds sometimes produce
_EXPANSION_ARTIFACT_HEADINGS: frozenset[str] = frozenset({
    "## Additional Information from Additional Files",
    "## Additional Files",
    "## New Classes and Functions",
    "## Updated Module Map",
    "## Additional Information",
    "## Additional Context",
    "## Additional Details",
})


def _normalize_h2(heading: str) -> str:
    """Strip parenthetical qualifiers from a ## heading for deduplication.

    ``"## Key Abstractions (Updated)"`` → ``"## Key Abstractions"``

    No regex — scans for trailing `` (...)`` pattern.
    """
    stripped = heading.rstrip()
    if not stripped.endswith(")"):
        return stripped

    # Find the opening paren that matches the trailing close paren.
    # Walk backwards from the second-to-last character.
    paren_start = stripped.rfind(" (")
    if paren_start < 0:
        return stripped

    # Verify no unclosed parens between paren_start and end
    candidate = stripped[paren_start + 2:-1]
    if "(" in candidate:
        return stripped

    return stripped[:paren_start].rstrip()


def _deduplicate_sections(doc: str) -> str:
    """Remove duplicate top-level (##) sections and known expansion artifacts.

    Multi-round expansion can produce:
    - Identical ``## Heading`` appearing more than once (keep first).
    - Headings with parenthetical qualifiers that are semantically duplicate.
    - Generic additive headings (always removed).

    Sub-sections (###) are not touched.
    """
    lines = doc.split("\n")
    seen_h2: set[str] = set()
    result: list[str] = []
    skipping = False

    for line in lines:
        if line.startswith("## "):
            heading = line.rstrip()
            normalized = _normalize_h2(heading)
            if heading in _EXPANSION_ARTIFACT_HEADINGS or normalized in seen_h2:
                skipping = True
            else:
                seen_h2.add(normalized)
                skipping = False
                result.append(line)
        elif skipping:
            pass
        else:
            result.append(line)

    return "\n".join(result)


def _deduplicate_subsections(doc: str) -> str:
    """Remove duplicate ### sub-sections within each ## section.

    If the same ### heading appears multiple times under the same
    ## parent, only the first occurrence is kept.

    No regex — uses simple string operations.
    """
    lines = doc.split("\n")
    result: list[str] = []
    # Track seen ### headings per current ## section
    seen_h3: set[str] = set()
    skipping_h3 = False

    for line in lines:
        if line.startswith("## "):
            # New top-level section — reset h3 tracking
            seen_h3 = set()
            skipping_h3 = False
            result.append(line)
        elif line.startswith("### "):
            heading = line.strip()
            if heading in seen_h3:
                skipping_h3 = True
            else:
                seen_h3.add(heading)
                skipping_h3 = False
                result.append(line)
        elif skipping_h3:
            # Skip content under a duplicate ### heading.
            # Stop skipping when we hit a new ### or ## heading (handled above).
            pass
        else:
            result.append(line)

    return "\n".join(result)


async def _generate_project_context_single_pass(
    repo_root: str,
    llm_client: "LLMClient",
    caps: dict[str, int],
    max_out: int,
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
    )

    if _appears_truncated(content):
        logger.warning(
            "Single-pass context output appears truncated (%d chars). "
            "Consider increasing LEAN_AI_OLLAMA_CONTEXT_WINDOW or "
            "LEAN_AI_OLLAMA_MAX_TOKENS.",
            len(content),
        )

    return _truncate_repetition(content)


async def _generate_project_context_multi_round(
    repo_root: str,
    llm_client: "LLMClient",
    caps: dict[str, int],
    max_out: int,
    context_window: int,
) -> str:
    """Multi-round project context generation for small context windows.

    Round 1: structural metadata + priority files.
    Rounds 2+: expand with additional file batches.
    Each round starts with a fresh context window.
    """
    from lean_ai.indexer.tree import list_repo_tree

    try:
        entries = list_repo_tree(repo_root)
    except Exception:
        logger.warning("multi-round: could not list repo tree, falling back to single-pass")
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

    # Parse additions into heading → [content_blocks].
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

    # Build heading → section end line mapping.
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


async def _expand_project_context(
    base_doc: str,
    repo_root: str,
    llm_client: "LLMClient",
    caps: dict[str, int],
    max_out: int,
    context_window: int,
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
        "expansion: merged %d batch result(s) (%d → %d chars)",
        len(valid_additions), len(base_doc), len(merged),
    )

    return merged


async def generate_project_context(
    repo_root: str,
    llm_client: "LLMClient",
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
        )
    else:
        logger.info(
            "Using single-pass context generation (context_window=%d)",
            settings.ollama_context_window,
        )
        content = await _generate_project_context_single_pass(
            repo_root, llm_client, caps, max_out,
        )

    content = _deduplicate_sections(content)
    content = _deduplicate_subsections(content)

    # ── Additive expansion: process remaining source files ──
    if settings.enable_multi_round_context:
        try:
            content = await _expand_project_context(
                content, repo_root, llm_client, caps, max_out,
                context_window=settings.ollama_context_window,
            )
        except Exception as exc:
            logger.warning("Additive expansion failed (non-fatal): %s", exc)

    # ── LLM-based semantic dedup (catches reformulated duplicates) ──
    try:
        from lean_ai.context.dedup import deduplicate_sections_llm

        content = await deduplicate_sections_llm(
            content, llm_client, log_prefix="Project context",
        )
    except Exception as exc:
        logger.warning("LLM-based dedup failed (non-fatal): %s", exc)

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
) -> str | None:
    """Incrementally update project_context.md with changes from modified files.

    Reads the current document, feeds the modified files through one additive
    expansion round, merges the result, and writes back.  Returns the output
    path, or ``None`` if no update was possible.

    Non-fatal — logs warnings on failure and returns ``None``.
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
        "update_project_context: done (%d → %d chars)",
        len(existing_doc), len(merged),
    )
    return path
