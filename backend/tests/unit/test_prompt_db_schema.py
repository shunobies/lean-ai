"""Unit tests for prompt versioning database schema changes.

Covers:
  1. prompt_versions table creation and column constraints
  2. prompt_variants table creation and column constraints
  3. ab_tests table creation and column constraints
  4. prompt_version_id column in training_traces table
  5. Migration idempotency — opening DB twice does not duplicate tables
"""

from __future__ import annotations

import pytest

from lean_ai.db import get_db
from lean_ai.training.db import get_training_db


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def main_db(tmp_path):
    """Main workspace DB connection scoped to a temp directory."""
    db = await get_db(str(tmp_path))
    yield db
    await db.close()


@pytest.fixture
async def train_db(tmp_path):
    """Training DB connection scoped to a temp directory."""
    db = await get_training_db(str(tmp_path))
    yield db
    await db.close()


# ── 1. prompt_versions table ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_versions_table_created(main_db):
    """The prompt_versions table is created by the schema migration."""
    cursor = await main_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prompt_versions'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, "prompt_versions table should exist"

    # Verify expected columns are present
    cursor = await main_db.execute("PRAGMA table_info(prompt_versions)")
    columns = {row["name"] for row in await cursor.fetchall()}
    expected_columns = {
        "id",
        "prompt_key",
        "version",
        "text",
        "variant_label",
        "is_active",
        "created_at",
    }
    assert expected_columns.issubset(
        columns
    ), f"Missing columns: {expected_columns - columns}"


# ── 2. prompt_variants table ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_variants_table_created(main_db):
    """The prompt_variants table is created by the schema migration."""
    cursor = await main_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prompt_variants'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, "prompt_variants table should exist"

    # Verify expected columns are present
    cursor = await main_db.execute("PRAGMA table_info(prompt_variants)")
    columns = {row["name"] for row in await cursor.fetchall()}
    expected_columns = {
        "id",
        "prompt_key",
        "variant_label",
        "weight",
        "created_at",
    }
    assert expected_columns.issubset(
        columns
    ), f"Missing columns: {expected_columns - columns}"


# ── 3. ab_tests table ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ab_tests_table_created(main_db):
    """The ab_tests table is created by the schema migration."""
    cursor = await main_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ab_tests'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, "ab_tests table should exist"

    # Verify expected columns are present
    cursor = await main_db.execute("PRAGMA table_info(ab_tests)")
    columns = {row["name"] for row in await cursor.fetchall()}
    expected_columns = {
        "id",
        "prompt_key",
        "name",
        "status",
        "start_date",
        "end_date",
        "created_at",
    }
    assert expected_columns.issubset(
        columns
    ), f"Missing columns: {expected_columns - columns}"


# ── 4. prompt_version_id in training_traces ─────────────────────────────


@pytest.mark.asyncio
async def test_prompt_version_id_column_in_training_traces(train_db):
    """The training_traces table has a prompt_version_id column for linking
    traces to specific prompt versions used during the turn."""
    cursor = await train_db.execute("PRAGMA table_info(training_traces)")
    columns = {row["name"] for row in await cursor.fetchall()}
    assert "prompt_version_id" in columns, (
        "training_traces should have a prompt_version_id column"
    )


# ── 5. Migration idempotency ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_idempotency(tmp_path):
    """Opening the database twice should not duplicate tables or raise errors.
    Both the main DB and training DB migrations must be idempotent."""
    # Open main DB twice
    db1 = await get_db(str(tmp_path))
    await db1.close()
    db2 = await get_db(str(tmp_path))

    # Verify prompt_versions table exists exactly once
    cursor = await db2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prompt_versions'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, "prompt_versions should exist exactly once after re-open"

    # Verify prompt_variants table exists exactly once
    cursor = await db2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prompt_variants'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, "prompt_variants should exist exactly once after re-open"

    # Verify ab_tests table exists exactly once
    cursor = await db2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ab_tests'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, "ab_tests should exist exactly once after re-open"

    await db2.close()

    # Open training DB twice
    tdb1 = await get_training_db(str(tmp_path))
    await tdb1.close()
    tdb2 = await get_training_db(str(tmp_path))

    # Verify prompt_version_id column still exists after re-open
    cursor = await tdb2.execute("PRAGMA table_info(training_traces)")
    columns = {row["name"] for row in await cursor.fetchall()}
    assert "prompt_version_id" in columns, (
        "prompt_version_id column should persist after re-open"
    )

    await tdb2.close()
