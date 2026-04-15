"""LLM-based project context generation and post-processing.

Contains the public API (generate, write, update).  Pure text processing
utilities live in ``text_processing.py``.

The primary generation approach is a **3-step pipeline**: a deterministic
skeleton is produced from metadata, then source files are processed in
batches (extraction), deduplicated/merged into the skeleton, and finally
condensed to a target size.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import (
    _CONDENSATION_PROMPT,
    _CONTEXT_GENERATION_CAP_TOKENS,
    _DEDUPLICATION_PROMPT,
    _EXTRACTION_PROMPT,
    _MAX_FILE_CHARS,
    CONDENSATION_MAX_WORDS,
    _dedup_input_budget,
    _extraction_batch_budget,
    _scale_generation_caps,
)
from .content import (
    _collect_all_ranked_candidates,
    batch_files_by_directory,
    build_condensation_user_prompt,
    build_dedup_user_prompt,
    build_deterministic_skeleton,
    build_extraction_user_prompt,
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


# ---------------------------------------------------------------------------
# Step 1: Raw extraction (per-batch, parallelisable)
# ---------------------------------------------------------------------------

async def _extract_batch(
    batch: list[tuple[str, str]],
    client: "LLMClient",
    max_tokens: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Run extraction on a single batch of source files."""
    user_msg = build_extraction_user_prompt(batch)
    msgs = [
        {"role": "system", "content": _EXTRACTION_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    result = await client.chat_raw(
        messages=msgs,
        max_tokens=max_tokens,
        thinking_callback=thinking_callback,
    )
    return _truncate_repetition(result)


async def _step1_extract(
    candidates: list[tuple[str, str]],
    client: "LLMClient",
    max_tokens: int,
    batch_budget: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[str]:
    """Extract facts from all source files in batches.

    Returns a list of raw extraction strings (one per batch).
    """
    from lean_ai.config import settings

    batches = batch_files_by_directory(candidates, batch_budget)
    total_batches = len(batches)
    if total_batches == 0:
        return []

    logger.info("Step 1: %d extraction batches from %d files", total_batches, len(candidates))

    raw_extractions: list[str] = []

    if settings.num_parallel >= 2 and total_batches > 1:
        # Parallel dispatch — semaphore in chat_raw() throttles concurrency.
        async def _run_batch(idx: int, batch: list[tuple[str, str]]) -> str | None:
            file_names = [p for p, _ in batch]
            await _emit_progress(
                progress_callback,
                phase="extraction",
                message=f"Extracting batch {idx + 1}/{total_batches} ({len(batch)} files)",
                current=idx,
                total=total_batches,
            )
            try:
                return await _extract_batch(batch, client, max_tokens, thinking_callback)
            except Exception as exc:
                logger.warning(
                    "Extraction batch %d/%d failed (non-fatal): %s — files: %s",
                    idx + 1, total_batches, exc, file_names,
                )
                return None

        results = await asyncio.gather(
            *(_run_batch(i, b) for i, b in enumerate(batches)),
        )
        for r in results:
            if r and r.strip():
                raw_extractions.append(r)
    else:
        # Sequential dispatch.
        for idx, batch in enumerate(batches):
            file_names = [p for p, _ in batch]
            await _emit_progress(
                progress_callback,
                phase="extraction",
                message=f"Extracting batch {idx + 1}/{total_batches} ({len(batch)} files)",
                current=idx,
                total=total_batches,
            )
            try:
                result = await _extract_batch(batch, client, max_tokens, thinking_callback)
                if result and result.strip():
                    raw_extractions.append(result)
            except Exception as exc:
                logger.warning(
                    "Extraction batch %d/%d failed (non-fatal): %s — files: %s",
                    idx + 1, total_batches, exc, file_names,
                )

    logger.info(
        "Step 1 complete: %d/%d batches produced extractions",
        len(raw_extractions), total_batches,
    )
    return raw_extractions


# ---------------------------------------------------------------------------
# Step 2: Deduplication & organisation
# ---------------------------------------------------------------------------

async def _step2_deduplicate(
    skeleton: str,
    raw_extractions: list[str],
    client: "LLMClient",
    max_tokens: int,
    max_input_chars: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Merge raw extractions into the skeleton, removing duplicates.

    If the combined input exceeds the context budget, processes extractions
    in chunks (iterative merge).  Falls back to programmatic merge on failure.
    """
    if not raw_extractions:
        return skeleton

    await _emit_progress(
        progress_callback,
        phase="deduplication",
        message="Deduplicating and organizing...",
    )

    all_extractions = "\n\n".join(raw_extractions)
    total_input = len(skeleton) + len(all_extractions)

    if total_input <= max_input_chars:
        # Single pass — everything fits.
        result = await _single_dedup_pass(
            skeleton, all_extractions, client, max_tokens, thinking_callback,
        )
        if result:
            return result
        # Fallback: programmatic merge.
        logger.warning("Step 2: LLM dedup failed, falling back to programmatic merge")
        return _programmatic_merge(skeleton, raw_extractions)

    # Iterative chunking — split extractions to fit budget.
    logger.info(
        "Step 2: input too large (%d chars > %d budget), using iterative chunking",
        total_input, max_input_chars,
    )
    document = skeleton
    chunks = _split_extractions_by_budget(raw_extractions, max_input_chars, len(skeleton))

    for i, chunk in enumerate(chunks):
        chunk_text = "\n\n".join(chunk)
        await _emit_progress(
            progress_callback,
            phase="deduplication",
            message=f"Deduplicating chunk {i + 1}/{len(chunks)}...",
        )
        result = await _single_dedup_pass(
            document, chunk_text, client, max_tokens, thinking_callback,
        )
        if result:
            document = result
        else:
            # Fallback for this chunk: programmatic merge.
            logger.warning(
                "Step 2: chunk %d/%d dedup failed, using programmatic merge",
                i + 1, len(chunks),
            )
            document = _merge_additions_into_doc(document, [chunk_text])

    return document


async def _single_dedup_pass(
    skeleton: str,
    extractions: str,
    client: "LLMClient",
    max_tokens: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str | None:
    """Run a single dedup LLM call. Returns ``None`` on failure or bad output."""
    user_msg = build_dedup_user_prompt(skeleton, extractions)
    msgs = [
        {"role": "system", "content": _DEDUPLICATION_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    try:
        result = await client.chat_raw(
            messages=msgs,
            max_tokens=max_tokens,
            thinking_callback=thinking_callback,
        )
        result = _truncate_repetition(result)

        # Sanity: output should be at least 70% of the skeleton length.
        if len(result) < len(skeleton) * 0.7:
            logger.warning(
                "Step 2: LLM output too short (%d chars vs %d skeleton), rejecting",
                len(result), len(skeleton),
            )
            return None

        return result
    except Exception as exc:
        logger.warning("Step 2: LLM dedup call failed: %s", exc)
        return None


def _split_extractions_by_budget(
    raw_extractions: list[str],
    max_input_chars: int,
    skeleton_chars: int,
) -> list[list[str]]:
    """Split raw extractions into chunks that fit the dedup input budget."""
    chunk_budget = max_input_chars - skeleton_chars - 2000  # headroom
    if chunk_budget < 2000:
        chunk_budget = 2000

    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for extraction in raw_extractions:
        ext_chars = len(extraction)
        if current_chars + ext_chars > chunk_budget and current:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(extraction)
        current_chars += ext_chars

    if current:
        chunks.append(current)

    return chunks


def _programmatic_merge(skeleton: str, raw_extractions: list[str]) -> str:
    """Fallback: merge extractions into skeleton programmatically."""
    from lean_ai.context.dedup import reorganize_sections

    merged = _merge_additions_into_doc(skeleton, raw_extractions)
    return reorganize_sections(merged, log_prefix="Step 2 fallback")


# ---------------------------------------------------------------------------
# Step 3: Condensation
# ---------------------------------------------------------------------------

async def _step3_condense(
    document: str,
    client: "LLMClient",
    max_tokens: int,
    context_window: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Condense the document to essential architectural knowledge.

    Skipped entirely if the document is already under the target word count.
    """
    effective = min(context_window, _CONTEXT_GENERATION_CAP_TOKENS)
    target_words = min(CONDENSATION_MAX_WORDS, int(effective * 0.05))

    current_words = len(document.split())
    if current_words <= target_words:
        logger.info(
            "Step 3: document already under target (%d <= %d words), skipping",
            current_words, target_words,
        )
        return document

    await _emit_progress(
        progress_callback,
        phase="condensation",
        message=f"Condensing ({current_words} words → target {target_words})...",
    )

    user_msg = build_condensation_user_prompt(document, target_words)
    system_prompt = _CONDENSATION_PROMPT.format(target_words=target_words)
    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        result = await client.chat_raw(
            messages=msgs,
            max_tokens=max_tokens,
            thinking_callback=thinking_callback,
        )
        result = _truncate_repetition(result)

        # Sanity: output should be between 40% and 110% of input length.
        ratio = len(result) / len(document) if document else 0
        if ratio < 0.4 or ratio > 1.1:
            logger.warning(
                "Step 3: output ratio %.2f out of range (0.4-1.1), keeping Step 2 output",
                ratio,
            )
            return document

        logger.info(
            "Step 3 complete: %d → %d chars (%.0f%%)",
            len(document), len(result), ratio * 100,
        )
        return result
    except Exception as exc:
        logger.warning("Step 3: condensation failed (non-fatal): %s", exc)
        return document


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_project_context(
    repo_root: str,
    llm_client: "LLMClient",
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
    progress_callback: ProgressCallback | None = None,
    worker_client: "LLMClient | None" = None,
    request_client: "LLMClient | None" = None,
) -> str:
    """Generate a project context document using a 3-step pipeline.

    **Phase 0 — Skeleton:** Deterministic structural overview from metadata.

    **Step 1 — Extraction:** Process source files in batches, extracting
    all notable facts.  Uses worker → request → primary model (first
    available).

    **Step 2 — Deduplication:** Merge raw extractions into the skeleton,
    removing duplicates.  Uses request → primary model.

    **Step 3 — Condensation:** Condense to essential knowledge within a
    size budget.  Uses primary model.
    """
    from lean_ai.config import settings

    logger.info("Generating project context for %s", repo_root)

    max_out = (
        settings.ollama_max_tokens
        or settings.ollama_context_window // 4
    )
    caps = _scale_generation_caps(settings.ollama_context_window, max_out)
    max_file = caps.get("max_file_chars", _MAX_FILE_CHARS)

    # ── Phase 0: deterministic skeleton (no LLM call) ────────────────
    await _emit_progress(
        progress_callback,
        phase="skeleton",
        message="Building structural skeleton...",
        current=0,
        total=0,
        chars=0,
    )

    skeleton = build_deterministic_skeleton(repo_root, section_caps=caps)
    write_project_context(repo_root, skeleton)
    logger.info("Phase 0 (skeleton) complete: %d chars", len(skeleton))

    # ── Collect source files ─────────────────────────────────────────
    try:
        from lean_ai.indexer.tree import list_repo_tree
        entries = list_repo_tree(repo_root)
    except Exception:
        entries = []

    from .metadata import extract_metadata_cached
    metadata = extract_metadata_cached(repo_root, entries=entries)

    candidates = _collect_all_ranked_candidates(
        repo_root,
        entries=entries,
        fan_in=metadata.fan_in,
        exclude_paths=set(),
        max_file_chars=max_file,
    )

    total_files = len(candidates)
    logger.info("Found %d source files to process", total_files)

    if total_files == 0:
        logger.info("No source files to process, returning skeleton")
        return skeleton

    # ── Model resolution ─────────────────────────────────────────────
    extraction_client = worker_client or request_client or llm_client
    dedup_client = request_client or llm_client
    condense_client = llm_client  # always primary

    context_window = settings.ollama_context_window
    batch_budget = _extraction_batch_budget(context_window, max_out)
    dedup_budget = _dedup_input_budget(context_window, max_out)

    # ── Step 1: Raw extraction ───────────────────────────────────────
    raw_extractions = await _step1_extract(
        candidates,
        extraction_client,
        max_tokens=max_out,
        batch_budget=batch_budget,
        thinking_callback=thinking_callback,
        progress_callback=progress_callback,
    )

    if not raw_extractions:
        logger.info("Step 1 produced no extractions, returning skeleton")
        write_project_context(repo_root, skeleton)
        return skeleton

    # ── Step 2: Deduplication & organisation ──────────────────────────
    content = await _step2_deduplicate(
        skeleton,
        raw_extractions,
        dedup_client,
        max_tokens=max_out,
        max_input_chars=dedup_budget,
        thinking_callback=thinking_callback,
        progress_callback=progress_callback,
    )

    write_project_context(repo_root, content)

    # ── Step 3: Condensation ─────────────────────────────────────────
    content = await _step3_condense(
        content,
        condense_client,
        max_tokens=max_out,
        context_window=context_window,
        thinking_callback=thinking_callback,
        progress_callback=progress_callback,
    )

    # ── Final cleanup (deterministic) ────────────────────────────────
    await _emit_progress(
        progress_callback,
        phase="cleanup",
        message="Final cleanup...",
        chars=len(content),
    )

    content = _deduplicate_sections(content)
    content = _deduplicate_subsections(content)

    from lean_ai.context.dedup import reorganize_sections
    content = reorganize_sections(content, log_prefix="Project context")

    write_project_context(repo_root, content)
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

    Uses the extraction prompt to extract facts from modified files, then
    merges them programmatically into the existing document.  Non-fatal —
    logs warnings on failure and returns ``None``.
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
    file_batch: list[tuple[str, str]] = []
    for rel_path in modified_paths:
        full = root / rel_path
        if not full.is_file():
            continue
        try:
            content = full.read_text(encoding="utf-8")[:max_file]
        except (UnicodeDecodeError, OSError):
            continue
        file_batch.append((rel_path, content))

    if not file_batch:
        logger.info("update_project_context: no readable modified files, skipping")
        return None

    logger.info(
        "update_project_context: extracting from %d modified file(s)",
        len(file_batch),
    )

    # Step 1: Extract facts from modified files.
    try:
        extraction = await _extract_batch(
            file_batch, llm_client, max_out, thinking_callback,
        )
        extraction = _truncate_repetition(extraction)
    except Exception as exc:
        logger.warning("update_project_context: extraction failed: %s", exc)
        return None

    if not extraction or not extraction.strip():
        logger.info("update_project_context: extraction produced no output, skipping")
        return None

    # Step 2: Programmatic merge (no LLM needed for incremental).
    merged = _merge_additions_into_doc(existing_doc, [extraction])

    from lean_ai.context.dedup import reorganize_sections
    merged = reorganize_sections(merged, log_prefix="Incremental update")

    path = write_project_context(repo_root, merged)
    logger.info(
        "update_project_context: done (%d -> %d chars)",
        len(existing_doc), len(merged),
    )
    return path
