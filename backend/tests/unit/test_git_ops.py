"""Tests for git commit message decoration helpers."""

from lean_ai.config import settings
from lean_ai.tools.git_ops import _with_coauthor_trailer


def test_coauthor_trailer_disabled(monkeypatch):
    monkeypatch.setattr(settings, "github_coauthor_enabled", False)
    monkeypatch.setattr(settings, "github_coauthor_name", "LeanAI")
    monkeypatch.setattr(settings, "github_coauthor_email", "leanai@timcomp.com")
    assert _with_coauthor_trailer("lean-ai: test") == "lean-ai: test"


def test_coauthor_trailer_uses_builtin_identity_when_blank(monkeypatch):
    monkeypatch.setattr(settings, "github_coauthor_enabled", True)
    monkeypatch.setattr(settings, "github_coauthor_name", "")
    monkeypatch.setattr(settings, "github_coauthor_email", "")
    assert (
        _with_coauthor_trailer("lean-ai: test")
        == "lean-ai: test\n\nCo-authored-by: LeanAI <leanai@timcomp.com>"
    )


def test_coauthor_trailer_appends_once(monkeypatch):
    monkeypatch.setattr(settings, "github_coauthor_enabled", True)
    monkeypatch.setattr(settings, "github_coauthor_name", "LeanAI")
    monkeypatch.setattr(settings, "github_coauthor_email", "leanai@timcomp.com")

    first = _with_coauthor_trailer("lean-ai: test")
    second = _with_coauthor_trailer(first)

    assert second.count("Co-authored-by: LeanAI <leanai@timcomp.com>") == 1
    assert second.endswith("Co-authored-by: LeanAI <leanai@timcomp.com>")
