"""SQLite-backed project context generation.

Contains the public API (generate, write, update).  Pure text processing
utilities live in ``text_processing.py``.

Pipeline:
  **Phase 0 — Skeleton:** Deterministic structural overview from metadata
  (no LLM call).  Entries inserted into the context database.

  **Phase 1 — Extraction:** Process source files ONE AT A TIME via LLM,
  parsing the output and inserting into the context database.  Parallel-safe
  via SQLite WAL mode.

  **Phase 2 — Export:** Export the database to Markdown, apply deterministic
  cleanup, and optionally condense via LLM.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import (
    _CONDENSATION_PROMPT,
    _EXTRACTION_PROMPT,
    _MAX_FILE_CHARS,
    CONDENSATION_MAX_WORDS,
    _scale_generation_caps,
)
from .content import (
    _collect_all_ranked_candidates,
    build_condensation_user_prompt,
    build_deterministic_skeleton,
)
from .context_db import (
    delete_entries_for_file,
    export_to_markdown,
    get_context_db,
    get_existing_hashes,
    upsert_entries_batch,
)
from .extraction_parser import ContextExtractionResult, parse_skeleton_output
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
    callback: ProgressCallback | None,
    **kwargs: object,
) -> None:
    """Fire a progress event if *callback* is set."""
    if callback:
        await callback(kwargs)


# ---------------------------------------------------------------------------
# Phase 1: Single-file extraction
# ---------------------------------------------------------------------------


def _compute_content_hash(file_content: str) -> str | None:
    """Compute SHA-256 hash of file content.

    Returns None on failure for fail-safe fallback.
    Testable seam: pure function that can be mocked in tests to control hash values.
    """
    try:
        return hashlib.sha256(file_content.encode("utf-8")).hexdigest()
    except Exception:
        logger.warning("Failed to compute content hash, falling back to standard extraction")
        return None


async def _extract_single_file(
    file_path: str,
    file_content: str,
    client: "LLMClient",
    max_tokens: int,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> list[tuple[str, str, str, str, str]]:
    """Extract facts from a single source file via LLM.

    Returns DB-ready ``(section, file_path, content, "llm", content_hash)`` tuples.
    Uses structured JSON output via ``chat_structured()`` — the response
    is a ``ContextExtractionResult`` validated against a Pydantic schema,
    which eliminates the heuristic markdown parsing the pipeline used
    previously.
    """
    content_hash = _compute_content_hash(file_content)
    if content_hash is None:
        content_hash = ""  # Fallback to empty string when hashing fails

    user_msg = f"=== SOURCE FILE: {file_path} ===\n```\n{file_content}\n```"
    msgs = [
        {"role": "system", "content": _EXTRACTION_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    result = await client.chat_structured(
        messages=msgs,
        schema=ContextExtractionResult,
        max_tokens=max_tokens,
        thinking_callback=thinking_callback,
    )
    tuples: list[tuple[str, str, str, str, str]] = []
    for entry in result.entries:
        entry_path = entry.file_path or file_path
        content = f"`{entry.symbol}` — {entry.description} (`{entry_path}`)"
        tuples.append((entry.section, entry_path, content, "llm", content_hash))
    return tuples


# ---------------------------------------------------------------------------
# Condensation (kept from previous pipeline)
# ---------------------------------------------------------------------------


async def _condense(
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
    target_words = min(CONDENSATION_MAX_WORDS, int(context_window * 0.05))

    current_words = len(document.split())
    if current_words <= target_words:
        logger.info(
            "Condensation: document already under target (%d <= %d words), skipping",
            current_words,
            target_words,
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

        ratio = len(result) / len(document) if document else 0
        if ratio < 0.4 or ratio > 1.1:
            logger.warning(
                "Condensation: output ratio %.2f out of range (0.4-1.1), keeping original",
                ratio,
            )
            return document

        logger.info(
            "Condensation complete: %d → %d chars (%.0f%%)",
            len(document),
            len(result),
            ratio * 100,
        )
        return result
    except Exception as exc:
        logger.warning("Condensation failed (non-fatal): %s", exc)
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
    """Generate project context via SQLite-backed file-by-file pipeline.

    **Phase 0 — Skeleton:** Deterministic structural overview from metadata.
    Inserted into the context database.

    **Phase 1 — Extraction:** Process source files ONE AT A TIME via LLM.
    Each file's extraction is parsed and inserted into the DB.  Uses
    worker → request → primary model (first available).  Parallel-safe
    via SQLite WAL mode.

    **Phase 2 — Export:** Export DB to Markdown, apply deterministic cleanup,
    and optionally condense via LLM (primary model).
    """
    from lean_ai.config import settings

    logger.info("Generating project context for %s", repo_root)

    max_out = settings.ollama_max_tokens or settings.ollama_context_window // 4
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
    logger.info("Phase 0 (skeleton) complete: %d chars", len(skeleton))

    db = await get_context_db(repo_root)

    # Load existing hashes for check-before-extract optimization
    existing_hashes = await get_existing_hashes(db)
    logger.info(f"Loaded {len(existing_hashes)} existing content hashes from cache")

    try:
        # Insert skeleton entries into DB.
        skeleton_entries = parse_skeleton_output(skeleton)
        inserted = await upsert_entries_batch(db, skeleton_entries)
        logger.info("Phase 0: inserted %d skeleton entries into DB", inserted)

        # ── Collect source files ────────────────────────────────────
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
            logger.info("No source files to process, exporting skeleton")
            content = await export_to_markdown(db)
            write_project_context(repo_root, content)
            return content

        # ── Model resolution ────────────────────────────────────────
        # Use the request model (chatty, larger context) for extraction when
        # available, falling back to the primary model.  Condensation always
        # uses the primary model.
        extraction_client = request_client or llm_client
        condense_client = llm_client

        # ── Phase 1: file-by-file extraction ────────────────────────
        context_window = settings.ollama_context_window

        if settings.num_parallel >= 2 and total_files > 1:
            await _phase1_parallel(
                candidates,
                extraction_client,
                max_out,
                repo_root,
                thinking_callback,
                progress_callback,
                existing_hashes=existing_hashes,
            )
        else:
            await _phase1_sequential(
                candidates,
                extraction_client,
                max_out,
                db,
                thinking_callback,
                progress_callback,
                existing_hashes=existing_hashes,
            )

        # Cleanup: remove entries for files no longer in the repository
        current_file_paths = {fp for fp, _ in candidates}
        all_db_paths = set(existing_hashes.keys())
        stale_paths = all_db_paths - current_file_paths
        if stale_paths:
            for stale_path in stale_paths:
                await delete_entries_for_file(db, stale_path)
                logger.info(f"Removed stale entry for deleted file: {stale_path}")
            logger.info(f"Cleaned up {len(stale_paths)} stale entries")

        # ── Phase 2: export + optional condensation ─────────────────
        await _emit_progress(
            progress_callback,
            phase="export",
            message="Exporting context from database...",
        )

        content = await export_to_markdown(db)

    finally:
        await db.close()

    # Deterministic cleanup.
    content = _deduplicate_sections(content)
    content = _deduplicate_subsections(content)

    from lean_ai.context.dedup import reorganize_sections

    content = reorganize_sections(content, log_prefix="Project context")

    # Optional condensation.
    content = await _condense(
        content,
        condense_client,
        max_tokens=max_out,
        context_window=context_window,
        thinking_callback=thinking_callback,
        progress_callback=progress_callback,
    )

    write_project_context(repo_root, content)
    logger.info("Project context generated: %d chars", len(content))
    return content


async def _phase1_sequential(
    candidates: list[tuple[str, str]],
    client: "LLMClient",
    max_tokens: int,
    db: "object",
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
    progress_callback: ProgressCallback | None = None,
    existing_hashes: dict[str, str] | None = None,
) -> None:
    """Process files sequentially, inserting into the shared DB connection."""
    total = len(candidates)
    for idx, (file_path, file_content) in enumerate(candidates):
        # Check-before-extract: skip if hash matches
        if existing_hashes is not None:
            current_hash = _compute_content_hash(file_content)
            if current_hash and file_path in existing_hashes and existing_hashes[file_path] == current_hash:
                logger.info(f"Skipping extraction for {file_path} (hash matches)")
                if progress_callback:
                    await progress_callback(idx + 1, total, file_path)
                continue

        await _emit_progress(
            progress_callback,
            phase="extraction",
            message=f"Extracting {idx + 1}/{total}: {file_path}",
            current=idx,
            total=total,
        )
        try:
            entries = await _extract_single_file(
                file_path,
                file_content,
                client,
                max_tokens,
                thinking_callback,
            )
            if entries:
                await upsert_entries_batch(db, entries)
        except Exception as exc:
            logger.warning(
                "Extraction %d/%d failed (non-fatal): %s — file: %s",
                idx + 1,
                total,
                exc,
                file_path,
            )

    logger.info("Phase 1 complete: processed %d files sequentially", total)


async def _phase1_parallel(
    candidates: list[tuple[str, str]],
    client: "LLMClient",
    max_tokens: int,
    repo_root: str,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
    progress_callback: ProgressCallback | None = None,
    existing_hashes: dict[str, str] | None = None,
) -> None:
    """Process files in parallel, each using its own DB connection for WAL safety."""
    # Pre-filter candidates against hash cache
    filtered_candidates: list[tuple[str, str]] = []
    for file_path, file_content in candidates:
        if existing_hashes is not None:
            current_hash = _compute_content_hash(file_content)
            if current_hash and file_path in existing_hashes and existing_hashes[file_path] == current_hash:
                logger.info(f"Skipping extraction for {file_path} (hash matches)")
                continue
        filtered_candidates.append((file_path, file_content))

    if not filtered_candidates:
        logger.info("All files already cached, skipping LLM extraction phase")
        return

    total = len(candidates)
    completed = 0

    async def _process_one(idx: int, file_path: str, file_content: str) -> None:
        nonlocal completed
        try:
            entries = await _extract_single_file(
                file_path,
                file_content,
                client,
                max_tokens,
                thinking_callback,
            )
            if not entries:
                return

            # Each coroutine opens its own connection for WAL-safe writes.
            file_db = await get_context_db(repo_root)
            try:
                await upsert_entries_batch(file_db, entries)
            finally:
                await file_db.close()
        except Exception as exc:
            logger.warning(
                "Extraction %d/%d failed (non-fatal): %s — file: %s",
                idx + 1,
                total,
                exc,
                file_path,
            )
        finally:
            completed += 1
            await _emit_progress(
                progress_callback,
                phase="extraction",
                message=f"Extracted {completed}/{total}: {file_path}",
                current=completed,
                total=total,
            )

    # Semaphore in chat_raw() throttles actual LLM concurrency.
    await asyncio.gather(
        *(_process_one(i, fp, fc) for i, (fp, fc) in enumerate(filtered_candidates)),
    )

    logger.info("Phase 1 complete: processed %d files in parallel", total)


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

    Deletes old entries for modified files, re-extracts via LLM, and
    re-exports the full document from the database.  Non-fatal — logs
    warnings on failure and returns ``None``.
    """
    from lean_ai.config import settings

    ctx_path = Path(repo_root) / ".lean_ai" / "project_context.md"
    if not ctx_path.is_file():
        logger.info("update_project_context: no existing context file, skipping")
        return None

    max_out = settings.ollama_max_tokens or settings.ollama_context_window // 4
    caps = _scale_generation_caps(settings.ollama_context_window, max_out)
    max_file = caps.get("max_file_chars", _MAX_FILE_CHARS)

    root = Path(repo_root)
    files_to_process: list[tuple[str, str]] = []
    for rel_path in modified_paths:
        full = root / rel_path
        if not full.is_file():
            continue
        try:
            content = full.read_text(encoding="utf-8")[:max_file]
        except (UnicodeDecodeError, OSError):
            continue
        files_to_process.append((rel_path, content))

    if not files_to_process:
        logger.info("update_project_context: no readable modified files, skipping")
        return None

    logger.info(
        "update_project_context: re-extracting %d modified file(s)",
        len(files_to_process),
    )

    db = await get_context_db(repo_root)
    try:
        for rel_path, file_content in files_to_process:
            # Remove stale entries for this file.
            await delete_entries_for_file(db, rel_path, source="llm")

            # Re-extract.
            try:
                entries = await _extract_single_file(
                    rel_path,
                    file_content,
                    llm_client,
                    max_out,
                    thinking_callback,
                )
                if entries:
                    await upsert_entries_batch(db, entries)
            except Exception as exc:
                logger.warning(
                    "update_project_context: extraction failed for %s: %s",
                    rel_path,
                    exc,
                )

        # Re-export.
        content = await export_to_markdown(db)
    finally:
        await db.close()

    from lean_ai.context.dedup import reorganize_sections

    content = reorganize_sections(content, log_prefix="Incremental update")

    path = write_project_context(repo_root, content)
    logger.info("update_project_context: done (%d chars)", len(content))
    return path


def _test_hash_skip_logic(file_content: str) -> str | None:
    """Test harness entry point for verifying hash computation and skip logic.

    Phase 5 tests can call this directly to verify hash computation determinism
    without needing full LLM infrastructure.
    """
    return _compute_content_hash(file_content)
