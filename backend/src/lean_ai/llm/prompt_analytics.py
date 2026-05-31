"""Analytics module for prompt version success rates and A/B comparisons.

Joins the workspace database (prompt_versions) with the training database
(training_traces) on prompt_version_id to compute per-version success
metrics. SQLite does not support cross-DB joins, so the join is performed
in Python after fetching from each database independently.

Orphaned traces — rows in training_traces whose prompt_version_id does
not reference any row in prompt_versions — are excluded from all
calculations to avoid incorrect metrics.
"""

from __future__ import annotations

import logging
from typing import Any

from lean_ai.db import get_db
from lean_ai.training.db import get_training_db

logger = logging.getLogger(__name__)


async def get_prompt_version_success_rates(
    repo_root: str,
    prompt_key: str,
) -> list[dict[str, Any]]:
    """Compute success rates for each version of a prompt.

    Joins prompt_versions (workspace DB) with training_traces (training DB)
    on prompt_version_id and aggregates success/failure counts per version.

    Args:
        repo_root: Path to the repository root (used to locate both DBs).
        prompt_key: The prompt key to filter versions by.

    Returns:
        A list of dicts, one per prompt version, each containing:
        - version_id: The prompt version row id.
        - version: The version number.
        - variant_label: The A/B variant label (e.g. 'control', 'treatment').
        - total_traces: Total number of training traces for this version.
        - successes: Number of traces with outcome 'success'.
        - success_rate: Fraction of successes (0.0 to 1.0).
    """
    main_db = await get_db(repo_root)
    train_db = await get_training_db(repo_root)

    try:
        # Fetch prompt version metadata from workspace DB
        cursor = await main_db.execute(
            "SELECT id, version, variant_label "
            "FROM prompt_versions "
            "WHERE prompt_key = ?",
            (prompt_key,),
        )
        version_rows = await cursor.fetchall()

        if not version_rows:
            return []

        # Build set of valid version IDs for filtering orphaned traces
        valid_ids = {row["id"] for row in version_rows}

        # Build metadata lookup: version_id -> {version, variant_label}
        version_meta: dict[int, dict[str, Any]] = {}
        for row in version_rows:
            version_meta[row["id"]] = {
                "version": row["version"],
                "variant_label": row["variant_label"],
            }

        # Fetch success stats from training DB, filtering to valid version IDs
        placeholders = ",".join("?" for _ in valid_ids)
        cursor = await train_db.execute(
            f"SELECT prompt_version_id, "
            f"COUNT(*) AS total, "
            f"SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes "
            f"FROM training_traces "
            f"WHERE prompt_version_id IN ({placeholders}) "
            f"GROUP BY prompt_version_id",
            tuple(valid_ids),
        )
        stats_rows = await cursor.fetchall()

        # Build stats lookup: version_id -> {total, successes}
        stats: dict[int, dict[str, int]] = {}
        for row in stats_rows:
            stats[row["prompt_version_id"]] = {
                "total": row["total"],
                "successes": row["successes"],
            }

        # Merge metadata with stats to produce analytics results
        results: list[dict[str, Any]] = []
        for vid, meta in version_meta.items():
            s = stats.get(vid, {"total": 0, "successes": 0})
            total = s["total"]
            successes = s["successes"]
            success_rate = successes / total if total > 0 else 0.0
            results.append(
                {
                    "version_id": vid,
                    "version": meta["version"],
                    "variant_label": meta["variant_label"],
                    "total_traces": total,
                    "successes": successes,
                    "success_rate": success_rate,
                }
            )

        return results
    finally:
        await main_db.close()
        await train_db.close()


async def compare_ab_variants(
    repo_root: str,
    prompt_key: str,
) -> dict[str, Any]:
    """Compare success rates between A/B test variants for a prompt.

    Fetches per-version success rates and groups them by variant_label
    to produce a comparison summary.

    Args:
        repo_root: Path to the repository root (used to locate both DBs).
        prompt_key: The prompt key to compare variants for.

    Returns:
        A dict containing:
        - prompt_key: The prompt key being compared.
        - variants: A dict mapping variant_label to a dict with
          success_rate, total_traces, and successes.
        - winner: The variant_label with the highest success rate,
          or None if no data is available.
    """
    rates = await get_prompt_version_success_rates(repo_root, prompt_key)

    # Group by variant_label, aggregating across versions with the same label
    variant_totals: dict[str, int] = {}
    variant_successes: dict[str, int] = {}

    for entry in rates:
        label = entry["variant_label"]
        if label is None:
            label = "unknown"
        variant_totals[label] = variant_totals.get(label, 0) + entry["total_traces"]
        variant_successes[label] = (
            variant_successes.get(label, 0) + entry["successes"]
        )

    # Build variant comparison dict
    variants: dict[str, dict[str, Any]] = {}
    for label in variant_totals:
        total = variant_totals[label]
        successes = variant_successes[label]
        success_rate = successes / total if total > 0 else 0.0
        variants[label] = {
            "success_rate": success_rate,
            "total_traces": total,
            "successes": successes,
        }

    # Determine winner
    winner: str | None = None
    if variants:
        winner = max(variants, key=lambda v: variants[v]["success_rate"])

    return {
        "prompt_key": prompt_key,
        "variants": variants,
        "winner": winner,
    }
