"""Utilities for identifying test files across all supported languages."""

from lean_ai.languages.registry import get_registry


def is_test_file_path(path: str) -> bool:
    """Return True if the given path is a test file.

    Uses the language registry's test patterns (directories, file prefixes,
    file suffixes) with a fallback for common conventions.
    """
    try:
        return get_registry().is_test_file(path)
    except Exception:
        norm = path.replace("\\", "/")
        parts = norm.split("/")
        filename = parts[-1] if parts else ""
        return (
            "tests/" in norm
            or "test/" in norm
            or "__tests__/" in norm
            or filename.startswith("test_")
            or filename.endswith("_test.py")
            or filename.endswith(".test.ts")
            or filename.endswith(".test.tsx")
            or filename.endswith(".test.js")
            or filename.endswith(".test.jsx")
            or filename.endswith(".spec.ts")
            or filename.endswith(".spec.tsx")
            or filename.endswith(".spec.js")
            or filename.endswith(".spec.jsx")
        )
