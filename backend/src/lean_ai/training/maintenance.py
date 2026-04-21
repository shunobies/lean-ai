"""Phase D automation: retention pruning + memory auto-promotion.

These tasks run opportunistically — never on the hot request path.
A small in-memory throttle avoids re-running the same workspace's
pass more often than once per hour, even if many sessions complete
in quick succession.
"""

from __future__ import annotations

import logging
import time

import aiosqlite

from lean_ai.config import settings
from lean_ai.memory.db import (
    bump_seen_count,
    find_similar_memory,
    update_curation_status,
)
from lean_ai.training.db import get_training_db, prune_expired

logger = logging.getLogger(__name__)


_RETENTION_THROTTLE_SECONDS = 3600.0
_last_retention_run: dict[str, float] = {}


async def run_retention_pass(
    repo_root: str,
    *,
    force: bool = False,
) -> dict:
    """Prune training archive rows older than ``training_retention_days``.

    Throttled to one run per workspace per hour unless ``force=True``.
    Returns the per-table deletion counts (empty dict on throttled or
    disabled).
    """
    if not settings.enable_training_capture:
        return {}
    now = time.monotonic()
    if not force:
        last = _last_retention_run.get(repo_root)
        if last is not None and (now - last) < _RETENTION_THROTTLE_SECONDS:
            return {}

    _last_retention_run[repo_root] = now
    try:
        db = await get_training_db(repo_root)
    except Exception:
        logger.debug("retention pass: could not open training db", exc_info=True)
        return {}
    try:
        counts = await prune_expired(db)
    finally:
        await db.close()
    return counts


async def auto_promote_memory(
    db: aiosqlite.Connection,
    *,
    category: str,
    content: str,
) -> dict | None:
    """Check whether an incoming memory matches an existing one.

    Returns the existing memory (with ``seen_count`` bumped and
    potentially promoted) if a match is found, else ``None`` — in which
    case the caller should proceed with a normal ``create_memory``.

    Promotion policy: when the existing memory's ``curation_status`` is
    ``auto`` and the bumped ``seen_count`` reaches
    ``settings.memory_autopromote_threshold`` (default 3), status is
    upgraded to ``high_confidence_auto`` so retrieval picks it up.
    """
    threshold = getattr(settings, "memory_autopromote_threshold", 3)
    similar = await find_similar_memory(db, content, category=category)
    if similar is None:
        return None
    updated = await bump_seen_count(
        db, similar["id"], promote_threshold=threshold,
    )
    if updated and updated.get("curation_status") != similar.get("curation_status"):
        logger.info(
            "Auto-promoted memory %s to %s (seen_count=%d)",
            similar["id"], updated["curation_status"], updated["seen_count"],
        )
    return updated


async def supersede_user_rejected(
    db: aiosqlite.Connection,
    *,
    category: str,
    content: str,
) -> bool:
    """If the matching memory was user-rejected, mark incoming as superseded.

    Returns True when we should skip the insert entirely (the user has
    already said "no" to this exact lesson).
    """
    similar = await find_similar_memory(db, content, category=category)
    if similar is None:
        return False
    if similar.get("curation_status") == "user_rejected":
        # User already dismissed this lesson — don't re-introduce it
        logger.info(
            "Skipping duplicate of user-rejected memory %s", similar["id"],
        )
        return True
    return False


async def bulk_invalidate_by_model(
    db: aiosqlite.Connection,
    *,
    model_name: str,
) -> int:
    """Mark every memory produced by *model_name* as superseded.

    Useful when a model regression is discovered — all its extractions
    can be demoted in one call rather than per-session.
    """
    cursor = await db.execute(
        "SELECT id FROM session_memories WHERE model_name = ? "
        "AND curation_status IN ('auto', 'high_confidence_auto')",
        (model_name,),
    )
    rows = await cursor.fetchall()
    count = 0
    for row in rows:
        if await update_curation_status(
            db, row[0], "superseded", confidence=0.0,
        ):
            count += 1
    logger.info(
        "Bulk-invalidated %d memories from model %s", count, model_name,
    )
    return count


def reset_throttle_for_tests() -> None:
    """Test helper: clear the retention throttle map."""
    _last_retention_run.clear()
