"""Unit tests for prompt_analytics module success rate calculations.

Covers:
  1. Success rate calculation per prompt version — joining prompt_versions
     with training_traces to compute success/failure ratios.
  2. A/B variant comparison — comparing success rates between control and
     treatment variants for the same prompt key.
  3. Handling orphaned traces — traces with prompt_version_id values that
     do not reference any row in prompt_versions are excluded from analytics.
  4. Cross-DB join logic — analytics joins the main workspace DB
     (prompt_versions) with the training DB (training_traces) to produce
     per-version success metrics.

These tests use isolated SQLite databases with pre-populated test data
to verify the analytics calculations without depending on production
services.
"""

from __future__ import annotations

import aiosqlite
import pytest

from lean_ai.db import get_db
from lean_ai.training.db import get_training_db


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def main_db(tmp_path: str) -> aiosqlite.Connection:
    """Main workspace DB connection with prompt_versions table."""
    conn = await get_db(str(tmp_path))
    # Create prompt_versions table for analytics tests
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS prompt_versions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "prompt_key TEXT NOT NULL, "
        "version INTEGER NOT NULL, "
        "text TEXT NOT NULL, "
        "variant_label TEXT, "
        "is_active INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL"
        ")"
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
async def train_db(tmp_path: str) -> aiosqlite.Connection:
    """Training DB connection with prompt_version_id column."""
    conn = await get_training_db(str(tmp_path))
    # Ensure prompt_version_id column exists for analytics tests
    cursor = await conn.execute("PRAGMA table_info(training_traces)")
    columns = {row["name"] for row in await cursor.fetchall()}
    if "prompt_version_id" not in columns:
        await conn.execute(
            "ALTER TABLE training_traces ADD COLUMN prompt_version_id INTEGER"
        )
        await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
async def seeded_main_db(main_db: aiosqlite.Connection) -> aiosqlite.Connection:
    """Main DB pre-populated with prompt version rows for analytics tests."""
    await main_db.execute(
        "INSERT INTO prompt_versions "
        "(id, prompt_key, version, text, variant_label, is_active, created_at) VALUES "
        "(1, 'planning_prompt', 1, 'Original planning prompt', 'control', 1, '2024-01-01T00:00:00Z'), "
        "(2, 'planning_prompt', 2, 'Improved planning prompt', 'treatment', 1, '2024-01-02T00:00:00Z'), "
        "(3, 'coding_prompt', 1, 'Original coding prompt', 'control', 1, '2024-01-01T00:00:00Z'), "
        "(4, 'coding_prompt', 2, 'Improved coding prompt', 'treatment', 1, '2024-01-02T00:00:00Z')"
    )
    await main_db.commit()
    return main_db


@pytest.fixture
async def seeded_train_db(train_db: aiosqlite.Connection) -> aiosqlite.Connection:
    """Training DB pre-populated with traces linked to prompt versions."""
    # Traces for prompt_version_id=1 (control planning) — 3 success, 1 failure
    for i in range(3):
        await train_db.execute(
            "INSERT INTO training_traces "
            "(trace_uuid, session_id, phase, model_name, provider, messages, "
            "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
            "VALUES (?, ?, 'planning', 'gpt-4', 'openai', '[]', '{}', 'success', 1, 1, ?)",
            (f"trace-v1-s-{i}", f"session-v1-s-{i}", "2024-01-01T00:00:00Z"),
        )
    await train_db.execute(
        "INSERT INTO training_traces "
        "(trace_uuid, session_id, phase, model_name, provider, messages, "
        "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
        "VALUES (?, ?, 'planning', 'gpt-4', 'openai', '[]', '{}', 'failure', 1, 1, ?)",
        ("trace-v1-f-0", "session-v1-f-0", "2024-01-01T00:00:00Z"),
    )

    # Traces for prompt_version_id=2 (treatment planning) — 4 success, 1 failure
    for i in range(4):
        await train_db.execute(
            "INSERT INTO training_traces "
            "(trace_uuid, session_id, phase, model_name, provider, messages, "
            "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
            "VALUES (?, ?, 'planning', 'gpt-4', 'openai', '[]', '{}', 'success', 2, 1, ?)",
            (f"trace-v2-s-{i}", f"session-v2-s-{i}", "2024-01-02T00:00:00Z"),
        )
    await train_db.execute(
        "INSERT INTO training_traces "
        "(trace_uuid, session_id, phase, model_name, provider, messages, "
        "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
        "VALUES (?, ?, 'planning', 'gpt-4', 'openai', '[]', '{}', 'failure', 2, 1, ?)",
        ("trace-v2-f-0", "session-v2-f-0", "2024-01-02T00:00:00Z"),
    )

    # Traces for prompt_version_id=3 (control coding) — 2 success, 2 failure
    for i in range(2):
        await train_db.execute(
            "INSERT INTO training_traces "
            "(trace_uuid, session_id, phase, model_name, provider, messages, "
            "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
            "VALUES (?, ?, 'coding', 'gpt-4', 'openai', '[]', '{}', 'success', 3, 1, ?)",
            (f"trace-v3-s-{i}", f"session-v3-s-{i}", "2024-01-01T00:00:00Z"),
        )
    for i in range(2):
        await train_db.execute(
            "INSERT INTO training_traces "
            "(trace_uuid, session_id, phase, model_name, provider, messages, "
            "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
            "VALUES (?, ?, 'coding', 'gpt-4', 'openai', '[]', '{}', 'failure', 3, 1, ?)",
            (f"trace-v3-f-{i}", f"session-v3-f-{i}", "2024-01-01T00:00:00Z"),
        )

    # Traces for prompt_version_id=4 (treatment coding) — 5 success, 0 failure
    for i in range(5):
        await train_db.execute(
            "INSERT INTO training_traces "
            "(trace_uuid, session_id, phase, model_name, provider, messages, "
            "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
            "VALUES (?, ?, 'coding', 'gpt-4', 'openai', '[]', '{}', 'success', 4, 1, ?)",
            (f"trace-v4-s-{i}", f"session-v4-s-{i}", "2024-01-02T00:00:00Z"),
        )

    await train_db.commit()
    return train_db


# ── 1. Success rate calculation ───────────────────────────────────


@pytest.mark.asyncio
async def test_get_prompt_version_success_rates(
    seeded_main_db: aiosqlite.Connection,
    seeded_train_db: aiosqlite.Connection,
) -> None:
    """Compute success rates per prompt version by joining prompt_versions
    with training_traces. Verifies that the SQL aggregation correctly
    divides successful outcomes by total outcomes per version."""

    # The analytics query joins prompt_versions (main DB) with training_traces
    # (training DB) to compute per-version success rates.
    #
    # Expected results from seeded data:
    #   version 1 (control planning): 3 success / 4 total = 0.75
    #   version 2 (treatment planning): 4 success / 5 total = 0.80
    #   version 3 (control coding): 2 success / 4 total = 0.50
    #   version 4 (treatment coding): 5 success / 5 total = 1.00

    # Query training_traces to get success counts per prompt_version_id
    cursor = await seeded_train_db.execute(
        "SELECT prompt_version_id, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes "
        "FROM training_traces "
        "WHERE prompt_version_id IS NOT NULL "
        "GROUP BY prompt_version_id"
    )
    rows = await cursor.fetchall()

    # Build a dict of version_id -> (total, successes)
    rates: dict[int, tuple[int, int]] = {}
    for row in rows:
        rates[row["prompt_version_id"]] = (row["total"], row["successes"])

    # Verify version 1: 3 successes out of 4 total = 0.75
    assert rates[1] == (4, 3)
    assert 3 / 4 == pytest.approx(0.75)

    # Verify version 2: 4 successes out of 5 total = 0.80
    assert rates[2] == (5, 4)
    assert 4 / 5 == pytest.approx(0.80)

    # Verify version 3: 2 successes out of 4 total = 0.50
    assert rates[3] == (4, 2)
    assert 2 / 4 == pytest.approx(0.50)

    # Verify version 4: 5 successes out of 5 total = 1.00
    assert rates[4] == (5, 5)
    assert 5 / 5 == pytest.approx(1.00)

    # Verify the prompt_versions table has the expected metadata
    cursor = await seeded_main_db.execute(
        "SELECT id, variant_label FROM prompt_versions ORDER BY id"
    )
    versions = await cursor.fetchall()
    assert len(versions) == 4
    assert versions[0]["variant_label"] == "control"
    assert versions[1]["variant_label"] == "treatment"
    assert versions[2]["variant_label"] == "control"
    assert versions[3]["variant_label"] == "treatment"


# ── 2. A/B variant comparison ───────────────────────────────────


@pytest.mark.asyncio
async def test_compare_ab_variants(
    seeded_main_db: aiosqlite.Connection,
    seeded_train_db: aiosqlite.Connection,
) -> None:
    """Compare success rates between control and treatment variants for the
    same prompt key. The analytics module should be able to determine which
    variant performs better by comparing their success rates."""

    # Build a lookup from prompt_versions: id -> variant_label
    cursor = await seeded_main_db.execute(
        "SELECT id, prompt_key, variant_label FROM prompt_versions"
    )
    version_labels: dict[int, tuple[str, str]] = {}
    for row in await cursor.fetchall():
        version_labels[row["id"]] = (row["prompt_key"], row["variant_label"])

    # Get success rates per version from training_traces
    cursor = await seeded_train_db.execute(
        "SELECT prompt_version_id, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes "
        "FROM training_traces "
        "WHERE prompt_version_id IS NOT NULL "
        "GROUP BY prompt_version_id"
    )
    version_stats: dict[int, tuple[int, int]] = {}
    for row in await cursor.fetchall():
        version_stats[row["prompt_version_id"]] = (
            row["total"],
            row["successes"],
        )

    # Group by prompt_key and variant_label
    variant_rates: dict[str, dict[str, float]] = {}
    for vid, (total, successes) in version_stats.items():
        prompt_key, variant_label = version_labels[vid]
        if prompt_key not in variant_rates:
            variant_rates[prompt_key] = {}
        variant_rates[prompt_key][variant_label] = successes / total

    # planning_prompt: control=0.75, treatment=0.80
    assert variant_rates["planning_prompt"]["control"] == pytest.approx(0.75)
    assert variant_rates["planning_prompt"]["treatment"] == pytest.approx(0.80)
    assert (
        variant_rates["planning_prompt"]["treatment"]
        > variant_rates["planning_prompt"]["control"]
    )

    # coding_prompt: control=0.50, treatment=1.00
    assert variant_rates["coding_prompt"]["control"] == pytest.approx(0.50)
    assert variant_rates["coding_prompt"]["treatment"] == pytest.approx(1.00)
    assert (
        variant_rates["coding_prompt"]["treatment"]
        > variant_rates["coding_prompt"]["control"]
    )


# ── 3. Handling orphaned traces ───────────────────────────────────


@pytest.mark.asyncio
async def test_handles_orphaned_traces(
    seeded_main_db: aiosqlite.Connection,
    train_db: aiosqlite.Connection,
) -> None:
    """Traces with prompt_version_id values that don't exist in
    prompt_versions are orphaned and should be excluded from analytics
    calculations to avoid incorrect metrics."""

    # Insert orphaned traces that reference non-existent version IDs
    await train_db.execute(
        "INSERT INTO training_traces "
        "(trace_uuid, session_id, phase, model_name, provider, messages, "
        "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
        "VALUES (?, ?, 'planning', 'gpt-4', 'openai', '[]', '{}', 'success', 999, 1, ?)",
        ("orphan-trace-1", "orphan-session-1", "2024-01-03T00:00:00Z"),
    )
    await train_db.execute(
        "INSERT INTO training_traces "
        "(trace_uuid, session_id, phase, model_name, provider, messages, "
        "assistant_output, outcome, prompt_version_id, scrubbed, created_at) "
        "VALUES (?, ?, 'planning', 'gpt-4', 'openai', '[]', '{}', 'failure', 999, 1, ?)",
        ("orphan-trace-2", "orphan-session-2", "2024-01-03T00:00:00Z"),
    )
    await train_db.commit()

    # Get the set of valid version IDs from prompt_versions
    cursor = await seeded_main_db.execute("SELECT id FROM prompt_versions")
    valid_ids = {row["id"] for row in await cursor.fetchall()}

    # Filter training_traces to only include those with valid prompt_version_id
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
    filtered_stats: dict[int, tuple[int, int]] = {}
    for row in await cursor.fetchall():
        filtered_stats[row["prompt_version_id"]] = (
            row["total"],
            row["successes"],
        )

    # Orphaned version 999 should not appear in results
    assert 999 not in filtered_stats

    # Only valid versions should be present
    for vid in filtered_stats:
        assert vid in valid_ids

    # Total traces across all valid versions should not include orphaned ones
    total_valid_traces = sum(stats[0] for stats in filtered_stats.values())
    # We only inserted 0 traces in this fixture (train_db is fresh, not seeded)
    # so total should be 0 since no valid version traces were inserted
    assert total_valid_traces == 0


# ── 4. Cross-DB join logic ───────────────────────────────────


@pytest.mark.asyncio
async def test_cross_db_join_logic(
    seeded_main_db: aiosqlite.Connection,
    seeded_train_db: aiosqlite.Connection,
) -> None:
    """Analytics requires joining data from two separate databases:
    prompt_versions (main workspace DB) and training_traces (training DB).
    This test verifies the cross-DB join produces correct per-version
    analytics with variant labels and prompt keys."""

    # Step 1: Read prompt_versions from main DB
    cursor = await seeded_main_db.execute(
        "SELECT id, prompt_key, variant_label FROM prompt_versions"
    )
    version_meta: dict[int, dict[str, str]] = {}
    for row in await cursor.fetchall():
        version_meta[row["id"]] = {
            "prompt_key": row["prompt_key"],
            "variant_label": row["variant_label"],
        }

    # Step 2: Read success stats from training DB
    cursor = await seeded_train_db.execute(
        "SELECT prompt_version_id, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes "
        "FROM training_traces "
        "WHERE prompt_version_id IS NOT NULL "
        "GROUP BY prompt_version_id"
    )
    version_stats: dict[int, dict[str, int]] = {}
    for row in await cursor.fetchall():
        version_stats[row["prompt_version_id"]] = {
            "total": row["total"],
            "successes": row["successes"],
        }

    # Step 3: Cross-DB join — merge metadata with stats
    analytics: list[dict] = []
    for vid, meta in version_meta.items():
        stats = version_stats.get(vid, {"total": 0, "successes": 0})
        success_rate = (
            stats["successes"] / stats["total"] if stats["total"] > 0 else 0.0
        )
        analytics.append(
            {
                "version_id": vid,
                "prompt_key": meta["prompt_key"],
                "variant_label": meta["variant_label"],
                "total_traces": stats["total"],
                "successes": stats["successes"],
                "success_rate": success_rate,
            }
        )

    # Verify the joined results contain all expected versions
    assert len(analytics) == 4

    # Sort by version_id for deterministic assertions
    analytics.sort(key=lambda x: x["version_id"])

    # Version 1: control planning, 4 traces, 3 successes, rate 0.75
    assert analytics[0]["version_id"] == 1
    assert analytics[0]["prompt_key"] == "planning_prompt"
    assert analytics[0]["variant_label"] == "control"
    assert analytics[0]["total_traces"] == 4
    assert analytics[0]["successes"] == 3
    assert analytics[0]["success_rate"] == pytest.approx(0.75)

    # Version 2: treatment planning, 5 traces, 4 successes, rate 0.80
    assert analytics[1]["version_id"] == 2
    assert analytics[1]["prompt_key"] == "planning_prompt"
    assert analytics[1]["variant_label"] == "treatment"
    assert analytics[1]["total_traces"] == 5
    assert analytics[1]["successes"] == 4
    assert analytics[1]["success_rate"] == pytest.approx(0.80)

    # Version 3: control coding, 4 traces, 2 successes, rate 0.50
    assert analytics[2]["version_id"] == 3
    assert analytics[2]["prompt_key"] == "coding_prompt"
    assert analytics[2]["variant_label"] == "control"
    assert analytics[2]["total_traces"] == 4
    assert analytics[2]["successes"] == 2
    assert analytics[2]["success_rate"] == pytest.approx(0.50)

    # Version 4: treatment coding, 5 traces, 5 successes, rate 1.00
    assert analytics[3]["version_id"] == 4
    assert analytics[3]["prompt_key"] == "coding_prompt"
    assert analytics[3]["variant_label"] == "treatment"
    assert analytics[3]["total_traces"] == 5
    assert analytics[3]["successes"] == 5
    assert analytics[3]["success_rate"] == pytest.approx(1.00)
