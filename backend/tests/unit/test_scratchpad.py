"""Tests for per-session scratchpad read/write/delete and size cap."""

import pytest

from lean_ai.tools.scratchpad import (
    SCRATCHPAD_MAX_CHARS,
    delete_scratchpad,
    read_scratchpad,
    scratchpad_path,
    update_scratchpad,
)

SESSION_ID = "abc123def456"


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary repo directory."""
    return str(tmp_path)


@pytest.mark.asyncio
async def test_update_and_read(tmp_repo):
    content = "## Completed\n- Fixed route\n## Next Step\n- Fix view"
    result = await update_scratchpad(content, tmp_repo, SESSION_ID)
    assert result.success
    assert read_scratchpad(tmp_repo, SESSION_ID) == content


@pytest.mark.asyncio
async def test_overwrite_replaces_content(tmp_repo):
    await update_scratchpad("first version", tmp_repo, SESSION_ID)
    await update_scratchpad("second version", tmp_repo, SESSION_ID)
    assert read_scratchpad(tmp_repo, SESSION_ID) == "second version"


@pytest.mark.asyncio
async def test_truncation_at_max_chars(tmp_repo):
    long_content = "x" * (SCRATCHPAD_MAX_CHARS + 500)
    await update_scratchpad(long_content, tmp_repo, SESSION_ID)
    stored = read_scratchpad(tmp_repo, SESSION_ID)
    assert "[SCRATCHPAD TRUNCATED" in stored
    # Original x's should be capped at SCRATCHPAD_MAX_CHARS
    assert stored.startswith("x" * SCRATCHPAD_MAX_CHARS)


def test_read_missing_returns_empty(tmp_repo):
    assert read_scratchpad(tmp_repo, SESSION_ID) == ""


def test_delete_removes_file(tmp_repo):
    path = scratchpad_path(tmp_repo, SESSION_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test content", encoding="utf-8")
    assert path.exists()
    delete_scratchpad(tmp_repo, SESSION_ID)
    assert not path.exists()


def test_delete_noop_when_missing(tmp_repo):
    # Should not raise
    delete_scratchpad(tmp_repo, SESSION_ID)


def test_scratchpad_path_location(tmp_repo):
    path = scratchpad_path(tmp_repo, SESSION_ID)
    assert str(path).endswith(f".lean_ai/scratchpads/{SESSION_ID}.md")
    assert str(path).startswith(tmp_repo)


def test_separate_sessions_have_separate_scratchpads(tmp_repo):
    """Each session gets its own scratchpad file."""
    path_a = scratchpad_path(tmp_repo, "session_aaa")
    path_b = scratchpad_path(tmp_repo, "session_bbb")
    assert path_a != path_b
    assert "session_aaa" in str(path_a)
    assert "session_bbb" in str(path_b)
