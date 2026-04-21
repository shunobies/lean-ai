"""Tests for the memory anonymizer (workspace-identifier stripping)."""

import pytest

from lean_ai.training.memory_anonymizer import (
    SymbolTable,
    anonymize_memories,
    anonymize_memory_content,
    build_symbol_table,
)


def _table(**overrides):
    kwargs = {
        "file_paths": frozenset(),
        "modules": frozenset(),
        "top_dirs": frozenset(),
        "symbols": frozenset(),
    }
    kwargs.update(overrides)
    return SymbolTable(**kwargs)


def test_absolute_paths_always_stripped():
    content = "See file at /home/user/project/src/auth/token.py for details"
    result = anonymize_memory_content(content, _table())
    assert "/home/user/project" not in result.content
    assert "<WORKSPACE_PATH>" in result.content


def test_file_path_replaced_with_workspace_file():
    content = "The JWT logic lives in src/auth/token.py."
    table = _table(file_paths=frozenset({"src/auth/token.py"}))
    result = anonymize_memory_content(content, table)
    assert "src/auth/token.py" not in result.content
    assert "<WORKSPACE_FILE>" in result.content


def test_longer_paths_replaced_first():
    content = "src/auth and src/auth/token.py both referenced"
    table = _table(file_paths=frozenset({
        "src/auth", "src/auth/token.py",
    }))
    result = anonymize_memory_content(content, table)
    # Longer path replaced first → both become the placeholder, nothing left
    assert "src/auth" not in result.content
    assert result.content.count("<WORKSPACE_FILE>") == 2


def test_module_name_replaced():
    content = "Import `myproject.auth.token` before use."
    table = _table(modules=frozenset({"myproject.auth.token"}))
    result = anonymize_memory_content(content, table)
    assert "myproject.auth.token" not in result.content
    assert "<WORKSPACE_MODULE>" in result.content


def test_symbol_only_replaced_inside_framing():
    # "MyWidget" is a known symbol; should only be redacted when framed as code
    content = "The MyWidget class is helpful. Just say mywidget casually."
    table = _table(symbols=frozenset({"MyWidget"}))
    result = anonymize_memory_content(content, table)
    # Framed occurrence → redacted
    assert "The MyWidget" not in result.content
    assert "<WORKSPACE_SYMBOL>" in result.content
    # Casual "mywidget" (different case, no framing) → preserved
    assert "mywidget casually" in result.content


def test_top_dir_slash_replaced():
    content = "Run from the backend/ root"
    table = _table(top_dirs=frozenset({"backend"}))
    result = anonymize_memory_content(content, table)
    assert "backend/" not in result.content
    assert "<WORKSPACE_PATH>/" in result.content


def test_ratio_reflects_redaction_amount():
    content = "Unique project specific content: /home/user/super-long-workspace-root"
    result = anonymize_memory_content(content, _table())
    assert result.ratio > 0.4


def test_generic_content_low_ratio():
    content = "When pytest fails with ModuleNotFoundError, check PYTHONPATH."
    result = anonymize_memory_content(content, _table())
    assert result.ratio == 0.0
    assert not result.dropped


def test_drop_threshold_triggered():
    # Content that's nearly all file path → should be dropped
    content = "/home/user/very/long/workspace/path/that/dominates/the/content"
    result = anonymize_memory_content(
        content, _table(), drop_threshold=0.5,
    )
    assert result.dropped


def test_drop_threshold_custom():
    content = "tiny /a/b/c path"
    # With a permissive threshold, not dropped
    keep = anonymize_memory_content(content, _table(), drop_threshold=0.9)
    assert not keep.dropped
    # With a strict threshold, dropped
    drop = anonymize_memory_content(content, _table(), drop_threshold=0.05)
    assert drop.dropped


def test_anonymize_memories_skips_dropped():
    rows = [
        {"id": "1", "content": "Generic lesson: use explicit imports."},
        {"id": "2", "content": "/a/b/c/d/e/f/g/h/i/j tiny"},
    ]
    result = list(anonymize_memories(
        iter(rows),
        table=_table(),
        drop_threshold=0.2,
    ))
    assert len(result) == 1
    assert result[0]["id"] == "1"
    assert "anonymization_ratio" in result[0]


@pytest.mark.asyncio
async def test_build_symbol_table_from_empty_workspace(tmp_path):
    from lean_ai.db import get_db

    db = await get_db(str(tmp_path))
    try:
        table = await build_symbol_table(db, repo_root=str(tmp_path))
        # No tool_logs, no top dirs → mostly empty table but no crash
        assert isinstance(table, SymbolTable)
        assert isinstance(table.file_paths, frozenset)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_build_symbol_table_captures_tool_log_paths(tmp_path):
    from lean_ai.db import get_db

    db = await get_db(str(tmp_path))
    try:
        # Seed a realistic tool log (JSON parameters column)
        await db.execute(
            "INSERT INTO tool_logs (session_id, tool_name, parameters, "
            "result, success, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess",
                "read_file",
                '{"file_path": "src/auth/token.py"}',
                "contents",
                1,
                "2026-04-20T00:00:00Z",
            ),
        )
        await db.execute(
            "INSERT INTO sessions (id, repo_root, task, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sess", str(tmp_path), "t", "active", "2026-04-20T00:00:00Z"),
        )
        await db.commit()

        table = await build_symbol_table(db, repo_root=str(tmp_path))
        assert any("token.py" in p for p in table.file_paths)
    finally:
        await db.close()
