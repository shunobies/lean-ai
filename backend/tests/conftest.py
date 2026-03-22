"""Global test fixtures.

Prevents the local backend/.env from leaking into tests that verify
default Settings values.  Without this, pydantic-settings reads .env
and tests like ``test_defaults_unchanged`` fail when the developer has
a customised .env file.
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
