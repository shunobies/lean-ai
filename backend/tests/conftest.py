"""Global test fixtures and session hooks.

Prevents the local backend/.env from leaking into tests that verify
default Settings values.  Without this, pydantic-settings reads .env
and tests like ``test_defaults_unchanged`` fail when the developer has
a customised .env file.

Also installs a ``pytest_sessionfinish`` hook that force-exits the
interpreter after pytest has printed its summary.  Without this, the
process sometimes hangs at futex during Python's interpreter shutdown
(the GIL-protected join on lingering async-mock coroutines that
pytest-asyncio's event-loop teardown did not fully collect).  Pytest
has already reported results by the time the hook fires, so the exit
code is preserved and nothing is lost.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings_from_env(monkeypatch, tmp_path):
    """Clear all LEAN_AI_* env vars so Settings() returns true defaults."""
    for key in list(os.environ):
        if key.startswith("LEAN_AI_"):
            monkeypatch.delenv(key, raising=False)

    # Point pydantic-settings at a non-existent .env so the real one
    # is never loaded.  The Settings model uses env_file=".env" which
    # resolves relative to cwd — changing cwd to a temp dir avoids it.
    monkeypatch.chdir(tmp_path)


# Stash the exit status so ``pytest_unconfigure`` can use it —
# ``pytest_sessionfinish`` receives it but runs before terminal-summary
# printing, while ``pytest_unconfigure`` runs after but does not.
_LAST_EXITSTATUS = {"value": 0}


def pytest_sessionfinish(session, exitstatus):
    """Record the session's final exit status for the force-exit hook."""
    _LAST_EXITSTATUS["value"] = int(exitstatus) if exitstatus is not None else 0


def pytest_unconfigure(config):
    """Force clean exit so the test suite never hangs at teardown.

    Back-to-back pytest runs were intermittently hanging at process
    exit for 40+ seconds after all 700+ tests passed.  The hang is a
    futex wait during Python's interpreter shutdown — unawaited
    AsyncMock coroutines garbage-collected during pytest's
    ``unraisableexception`` finalizer leave async state that the main
    thread waits on indefinitely.  ``pytest_unconfigure`` fires AFTER
    the terminal reporter has printed the final summary, so exit code
    and output are preserved when we force-exit with ``os._exit``.
    Skipping atexit handlers is fine because the suite does not rely
    on any.  Only active under real pytest sessions — collection-only
    runs (``--collect-only``) still use the normal exit path so
    plugins can inspect collection output.
    """
    if config.getoption("--collect-only", default=False):
        return
    # Flush stdio so the summary is fully visible before the hard exit.
    import sys
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(_LAST_EXITSTATUS["value"])
