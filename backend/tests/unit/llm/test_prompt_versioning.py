"""Unit tests for PromptRegistry versioning and A/B testing functionality.

Covers:
  1. Deterministic hashing — same session ID always maps to the same variant.
  2. Variant distribution — different sessions are distributed across variants.
  3. save_version persistence — versions are persisted to the SQLite database.
  4. get() returns PromptVersionResult — the new return shape is correct.
  5. format() returns PromptVersionResult — formatting preserves version metadata.
  6. Backward compatibility — calls without a session_id still work.
"""

from __future__ import annotations

import aiosqlite
import pytest

from lean_ai.db import get_db
from lean_ai.llm.prompt_registry import PromptEntry, PromptRegistry


@pytest.fixture
async def db(tmp_path: str) -> aiosqlite.Connection:
    """Open a temporary workspace database with prompt versioning schema."""
    conn = await get_db(str(tmp_path))
    yield conn
    await conn.close()


@pytest.fixture
def registry() -> PromptRegistry:
    """Return a fresh PromptRegistry with a test prompt registered."""
    reg = PromptRegistry()
    reg.register(
        PromptEntry(
            key="test_prompt",
            category="Testing",
            name="Test Prompt",
            description="A prompt used for unit tests",
            default_text="Hello, {name}! Welcome to {place}.",
            template_vars=["name", "place"],
        )
    )
    return reg


# ── Deterministic hashing ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deterministic_hashing_same_session_same_variant(
    registry: PromptRegistry,
    db: aiosqlite.Connection,
) -> None:
    """The same session_id must always resolve to the same variant."""
    session_id = "deterministic-session-001"

    # Save two variants for the prompt
    await registry.save_version(
        db,
        prompt_key="test_prompt",
        version=1,
        text="Hello, {name}! Welcome to {place}.",
        variant_label="control",
    )
    await registry.save_version(
        db,
        prompt_key="test_prompt",
        version=2,
        text="Hi {name}, you're at {place}!",
        variant_label="treatment",
    )

    # Call get multiple times with the same session_id
    result_a = await registry.get(db, "test_prompt", session_id=session_id)
    result_b = await registry.get(db, "test_prompt", session_id=session_id)

    assert result_a.variant_label == result_b.variant_label


# ── Variant distribution ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_variant_distribution_across_sessions(
    registry: PromptRegistry,
    db: aiosqlite.Connection,
) -> None:
    """Different session_ids should be distributed across available variants."""
    # Save two variants
    await registry.save_version(
        db,
        prompt_key="test_prompt",
        version=1,
        text="Hello, {name}! Welcome to {place}.",
        variant_label="control",
    )
    await registry.save_version(
        db,
        prompt_key="test_prompt",
        version=2,
        text="Hi {name}, you're at {place}!",
        variant_label="treatment",
    )

    # Collect variant labels across many sessions
    labels: list[str] = []
    for i in range(100):
        session_id = f"distribution-session-{i}"
        result = await registry.get(db, "test_prompt", session_id=session_id)
        labels.append(result.variant_label)

    # Both variants should appear at least once
    assert "control" in labels
    assert "treatment" in labels


# ── save_version persistence ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_version_persists_to_db(
    registry: PromptRegistry,
    db: aiosqlite.Connection,
) -> None:
    """save_version must persist version rows to the prompt_versions table."""
    await registry.save_version(
        db,
        prompt_key="test_prompt",
        version=1,
        text="Hello, {name}! Welcome to {place}.",
        variant_label="control",
    )

    # Verify the row exists in the database
    cursor = await db.execute(
        "SELECT prompt_key, version, variant_label FROM prompt_versions "
        "WHERE prompt_key = ? AND version = ?",
        ("test_prompt", 1),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "test_prompt"
    assert row[1] == 1
    assert row[2] == "control"


# ── get returns PromptVersionResult ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_prompt_version_result(
    registry: PromptRegistry,
    db: aiosqlite.Connection,
) -> None:
    """get() with a session_id should return a PromptVersionResult object."""
    await registry.save_version(
        db,
        prompt_key="test_prompt",
        version=1,
        text="Hello, {name}! Welcome to {place}.",
        variant_label="control",
    )

    result = await registry.get(db, "test_prompt", session_id="test-session")

    from lean_ai.llm.prompt_registry import PromptVersionResult

    assert isinstance(result, PromptVersionResult)
    assert result.text == "Hello, {name}! Welcome to {place}."
    assert result.variant_label == "control"
    assert result.version == 1


# ── format returns PromptVersionResult ───────────────────────────────────────


@pytest.mark.asyncio
async def test_format_returns_prompt_version_result(
    registry: PromptRegistry,
    db: aiosqlite.Connection,
) -> None:
    """format() with a session_id should return a PromptVersionResult with
    the formatted text."""
    await registry.save_version(
        db,
        prompt_key="test_prompt",
        version=1,
        text="Hello, {name}! Welcome to {place}.",
        variant_label="control",
    )

    result = await registry.format(
        db,
        "test_prompt",
        session_id="format-session",
        name="Alice",
        place="Wonderland",
    )

    from lean_ai.llm.prompt_registry import PromptVersionResult

    assert isinstance(result, PromptVersionResult)
    assert result.text == "Hello, Alice! Welcome to Wonderland."
    assert result.variant_label == "control"


# ── Backward compatibility ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backward_compat_no_session_id(
    registry: PromptRegistry,
    db: aiosqlite.Connection,
) -> None:
    """Calling get() without a session_id should still return the default text
    as a plain string for backward compatibility."""
    result = await registry.get(db, "test_prompt")

    assert isinstance(result, str)
    assert result == "Hello, {name}! Welcome to {place}."
