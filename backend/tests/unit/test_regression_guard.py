"""Tests for Layer 7 regression test protection.

The regression guard identifies files matching the project-wide
regression-file convention so they can be protected from edits by the
tool executor and flagged by the fix-loop prompt.
"""

from __future__ import annotations

import pytest

from lean_ai.tools.regression_guard import (
    REGRESSION_GUARD_ERROR,
    extract_regression_paths_from_text,
    is_regression_test_path,
    regression_test_paths,
)


@pytest.mark.parametrize("path", [
    "regression_foo_test.py",
    "regression-bar.spec.ts",
    "tests/regression/foo_test.py",
    "tests/regression/bar.spec.ts",
    "spec/regression/baz_spec.rb",
    "src/regressions/core.py",
    "path/to/regressions/deep/nested.go",
    "tests/regression/FooTest.java",
])
def test_matches_regression_paths(path: str) -> None:
    assert is_regression_test_path(path), f"should match: {path}"


@pytest.mark.parametrize("path", [
    "tests/test_foo.py",
    "src/service.py",
    "test_regression_logic.py",  # "regression" is inside a filename, no path component
    "regression.log",            # log file, not a test file
    "notes/regression.md",       # doc, not a code extension
    "regression",                # bare word with no extension
    "",
])
def test_does_not_match_non_regression_paths(path: str) -> None:
    assert not is_regression_test_path(path), f"should NOT match: {path}"


def test_windows_style_paths_are_normalized() -> None:
    assert is_regression_test_path("tests\\regression\\foo_test.py")
    assert is_regression_test_path("spec\\regression-bar.spec.ts")


def test_filter_preserves_order_and_deduplicates() -> None:
    paths = [
        "src/foo.py",
        "tests/regression/a_test.py",
        "regression_b_test.py",
        "tests/regression/a_test.py",  # duplicate — dropped
        "docs.md",
    ]
    assert regression_test_paths(paths) == [
        "tests/regression/a_test.py",
        "regression_b_test.py",
    ]


def test_extracts_regression_paths_from_pytest_output() -> None:
    text = (
        "FAILED tests/regression/login_test.py::test_returning_bug - AssertionError\n"
        "  File 'tests/regression/login_test.py', line 12\n"
        "  ok: tests/test_normal.py::test_basic passed\n"
    )
    assert extract_regression_paths_from_text(text) == [
        "tests/regression/login_test.py",
    ]


def test_extract_empty_when_no_regression_paths() -> None:
    text = "FAILED tests/test_foo.py::test_bar - AssertionError"
    assert extract_regression_paths_from_text(text) == []
    assert extract_regression_paths_from_text("") == []


def test_guard_error_message_contains_path() -> None:
    msg = REGRESSION_GUARD_ERROR.format(path="tests/regression/demo_test.py")
    assert "tests/regression/demo_test.py" in msg
    assert "IMPLEMENTATION" in msg
