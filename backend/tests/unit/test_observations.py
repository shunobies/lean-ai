"""Tests for Phase 2 structured file observations."""

import pytest

from lean_ai.tools.observations import (
    _MAX_KEY_SNIPPETS,
    _MAX_KEY_SNIPPET_CHARS,
    read_observations,
    record_observation,
)


@pytest.mark.asyncio
async def test_record_observation_wraps_single_string_snippet(tmp_path):
    snippet = "def git_add_and_commit_attribution(message: str) -> None:\n    pass"

    result = await record_observation(
        file_path="backend/src/example.py",
        role="modify",
        reason="needs a call site",
        key_snippets=snippet,  # type: ignore[arg-type]
        repo_root=str(tmp_path),
        session_id="session-1",
    )

    assert result.success
    observations = read_observations(str(tmp_path), "session-1")
    assert observations[0]["key_snippets"] == [snippet]


@pytest.mark.asyncio
async def test_record_observation_collapses_character_list_snippet(tmp_path):
    snippet = "line 1\nline 2\nline 3"

    await record_observation(
        file_path="backend/src/example.py",
        role="reference",
        reason="shows the pattern",
        key_snippets=list(snippet),
        repo_root=str(tmp_path),
        session_id="session-1",
    )

    observations = read_observations(str(tmp_path), "session-1")
    assert observations[0]["key_snippets"] == [snippet]


@pytest.mark.asyncio
async def test_record_observation_caps_snippet_count_and_size(tmp_path):
    long_snippet = "x" * (_MAX_KEY_SNIPPET_CHARS + 100)

    await record_observation(
        file_path="backend/src/example.py",
        role="modify",
        reason="contains relevant behavior",
        key_snippets=[long_snippet] * (_MAX_KEY_SNIPPETS + 2),
        repo_root=str(tmp_path),
        session_id="session-1",
    )

    observations = read_observations(str(tmp_path), "session-1")
    snippets = observations[0]["key_snippets"]
    assert len(snippets) == _MAX_KEY_SNIPPETS
    assert snippets[0].endswith("... [truncated]")
    assert len(snippets[0]) < len(long_snippet)
