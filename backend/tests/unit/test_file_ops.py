"""Tests for file operation edge cases."""

import pytest


@pytest.mark.asyncio
async def test_read_file_pages_large_text_file_with_line_range(tmp_path):
    """Large saved outputs can be paged with start_line/end_line."""
    from lean_ai.tools.file_ops import read_file

    line_payload = "x" * 120
    lines = [f"line {i} {line_payload}" for i in range(1, 25_000)]
    (tmp_path / "large.log").write_text("\n".join(lines), encoding="utf-8")

    result = await read_file(
        "large.log",
        str(tmp_path),
        start_line=1000,
        end_line=1002,
    )

    assert result.success
    assert "1000 | line 1000" in (result.output or "")
    assert "1002 | line 1002" in (result.output or "")
    assert "File too large" not in (result.output or "")


@pytest.mark.asyncio
async def test_read_file_accepts_string_line_numbers(tmp_path):
    """Tool-call line numbers may arrive as strings from a model."""
    from lean_ai.tools.file_ops import read_file

    (tmp_path / "small.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = await read_file(
        "small.txt",
        str(tmp_path),
        start_line="2",
        end_line="2",
    )

    assert result.success
    assert result.output == "   2 | two"
