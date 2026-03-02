"""LLM-based project context generation and post-processing.

Contains the single-pass and multi-round generation strategies, as well as
repetition detection, section deduplication, and file output.

No regex — all text processing uses simple string operations.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import (
    _CONTEXT_GENERATION_SYSTEM_PROMPT,
    _EXPANSION_SYSTEM_PROMPT,
    _MAX_DOC_FILE_CHARS,
    _MAX_FILE_CHARS,
    _scale_generation_caps,
)
from .content import (
    _batch_file_contents,
    _build_expansion_prompt,
    _collect_all_ranked_candidates,
    _collect_priority_file_contents,
    build_generation_prompt,
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
        temperature=0.1,
        max_tokens=max_out,
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
        temperature=0.1,
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
            temperature=0.1,
            max_tokens=max_out,
        )
        updated_doc = _truncate_repetition(updated_doc)

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
