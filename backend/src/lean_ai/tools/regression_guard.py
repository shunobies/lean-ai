"""Regression test protection (Layer 7).

A regression test is a test that prevents a previously-fixed bug or
load-bearing behavior from returning. Regression tests are IMMUTABLE
once a plan completes — if one fails, the implementation is wrong;
the test must not be edited.

Convention (configurable via ``settings.regression_file_pattern``):
- Paths whose basename starts with ``regression_`` and ends in a file
  extension (e.g. ``regression_login_test.py``, ``regression-foo.spec.ts``).
- Paths containing a ``/regression/`` or ``/regressions/`` directory
  component (e.g. ``tests/regression/auth_test.py``).

Deliberately NOT matched:
- ``test_regression_logic.py`` — ``regression`` is part of a
  non-regression test's module name, not a path-component prefix.
- ``regression.log``, ``regression.md`` — non-test / non-code files.

The tool executor calls ``is_regression_test_path`` before any
``edit_file`` to reject edits on finalized regression files. Files
created during the CURRENT plan are tracked separately (see
``session_created_regression_files`` on the workflow session state)
and remain editable until the plan completes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from lean_ai.config import settings

REGRESSION_GUARD_ERROR = (
    "ERROR: '{path}' is a regression test and MUST NOT be modified. "
    "If a regression test fails, the IMPLEMENTATION is wrong — edit "
    "the production code to restore the tested behavior. Regression "
    "tests are only editable via a dedicated regression-update "
    "workflow, never through a plan or fix-loop edit."
)


@lru_cache(maxsize=1)
def _compiled_pattern() -> re.Pattern[str]:
    """Compile the configured regression pattern once per process.

    ``lru_cache`` means changing the env var at runtime requires a
    process restart — acceptable for a pattern that exists to enforce
    long-term invariants.
    """
    return re.compile(settings.regression_file_pattern)


def is_regression_test_path(path: str) -> bool:
    """Return True when ``path`` matches the regression file convention.

    Normalizes backslashes to forward slashes before matching so
    Windows-style paths are handled correctly. Accepts absolute or
    repo-relative paths.
    """
    if not path:
        return False
    normalized = path.replace("\\", "/")
    return bool(_compiled_pattern().search(normalized))


def regression_test_paths(paths: Iterable[str]) -> list[str]:
    """Filter an iterable of paths down to the regression tests.

    Preserves input order and deduplicates.
    """
    seen: set[str] = set()
    hits: list[str] = []
    for p in paths:
        if p and p not in seen and is_regression_test_path(p):
            hits.append(p)
            seen.add(p)
    return hits


_PATH_TOKEN_RE = re.compile(r"[\w./\\-]+")


def extract_regression_paths_from_text(text: str) -> list[str]:
    """Best-effort extraction of regression-test paths mentioned in
    free-form output (e.g. a failing pytest stdout).

    Used by the fix-loop banner to decide whether to warn the LLM
    that it's about to touch a regression test. Returns deduplicated
    path-like tokens that match the regression convention.
    """
    if not text:
        return []
    tokens = _PATH_TOKEN_RE.findall(text)
    return regression_test_paths(tokens)
