"""Process-wide flags indicating long-running work in progress.

Read by the ``/api/health`` endpoint so extension-side health monitors can
distinguish "backend is slow because it's busy" from "backend is dead".

Typical usage:

    from lean_ai.runtime_state import busy

    with busy("embeddings.code"):
        ...  # long-running work

The context manager is idempotent (missing tags on ``mark_idle`` are a
no-op) and thread-safe (a lock guards the underlying set).
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

_busy_tasks: set[str] = set()
_lock = Lock()


def mark_busy(task: str) -> None:
    """Register that ``task`` is actively running. Idempotent."""
    with _lock:
        _busy_tasks.add(task)


def mark_idle(task: str) -> None:
    """Deregister ``task``. Idempotent — missing tag is a no-op."""
    with _lock:
        _busy_tasks.discard(task)


def current_busy() -> list[str]:
    """Return a sorted snapshot of active task tags."""
    with _lock:
        return sorted(_busy_tasks)


@contextmanager
def busy(task: str):
    """Context-manager form. Ensures ``mark_idle`` even on exception."""
    mark_busy(task)
    try:
        yield
    finally:
        mark_idle(task)
