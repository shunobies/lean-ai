"""Tests for session memory Whoosh search index."""

import pytest

from lean_ai.memory.index import (
    index_memory,
    remove_memory,
    search_memories,
)


@pytest.fixture
def repo(tmp_path):
    """Temporary workspace root."""
    return str(tmp_path)


def test_index_and_search(repo):
    index_memory(
        repo,
        memory_id="mem_001",
        content="pytest requires conftest.py at the repository root for fixtures",
        category="testing",
        tags=["pytest", "fixtures"],
        source_task="Fix test discovery issues",
    )

    results = search_memories(repo, "pytest fixtures repository")
    assert len(results) == 1
    assert results[0]["memory_id"] == "mem_001"
    assert results[0]["category"] == "testing"
    assert "conftest" in results[0]["content"]


def test_search_by_tags(repo):
    index_memory(repo, "m1", "use ruff for formatting", "convention", ["ruff", "lint"])
    index_memory(repo, "m2", "database uses WAL mode", "architecture", ["sqlite"])

    results = search_memories(repo, "ruff lint")
    assert len(results) >= 1
    assert results[0]["memory_id"] == "m1"


def test_search_by_source_task(repo):
    index_memory(
        repo,
        "m1",
        "API requires auth header",
        "gotcha",
        ["auth"],
        "Fix API authentication",
    )

    results = search_memories(repo, "authentication")
    assert len(results) == 1
    assert results[0]["memory_id"] == "m1"


def test_search_empty_index(repo):
    results = search_memories(repo, "anything")
    assert results == []


def test_remove_memory(repo):
    index_memory(repo, "m1", "some memory", "pattern")
    remove_memory(repo, "m1")

    results = search_memories(repo, "some memory")
    assert results == []


def test_update_existing(repo):
    index_memory(repo, "m1", "old content", "gotcha")
    index_memory(repo, "m1", "updated content", "gotcha")

    results = search_memories(repo, "updated content")
    assert len(results) == 1
    assert "updated" in results[0]["content"]

    # Old content should not be found
    old_results = search_memories(repo, "old content")
    assert len(old_results) == 0


def test_search_limit(repo):
    for i in range(10):
        index_memory(repo, f"m{i}", f"memory about testing item {i}", "testing")

    results = search_memories(repo, "testing", limit=3)
    assert len(results) == 3


def test_search_relevance_order(repo):
    index_memory(repo, "m1", "unrelated topic about cooking", "pattern")
    index_memory(
        repo,
        "m2",
        "pytest fixtures require conftest.py at repo root",
        "testing",
        ["pytest"],
    )

    results = search_memories(repo, "pytest fixtures repo root")
    assert len(results) >= 1
    assert results[0]["memory_id"] == "m2"
