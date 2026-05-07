"""Tests for git commit message decoration helpers."""

import subprocess

import pytest

from lean_ai.config import settings
from lean_ai.tools.git_ops import _with_coauthor_trailer, git_commit, git_stash_pop, git_stash_push


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in a test repository."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_repo(repo) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("base\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")


def test_coauthor_trailer_disabled(monkeypatch):
    monkeypatch.setattr(settings, "github_coauthor_enabled", False)
    monkeypatch.setattr(settings, "github_coauthor_name", "LeanAI-bot")
    monkeypatch.setattr(settings, "github_coauthor_email", "leanai@timcomp.com")
    assert _with_coauthor_trailer("lean-ai: test") == "lean-ai: test"


def test_coauthor_trailer_uses_builtin_identity_when_blank(monkeypatch):
    monkeypatch.setattr(settings, "github_coauthor_enabled", True)
    monkeypatch.setattr(settings, "github_coauthor_name", "")
    monkeypatch.setattr(settings, "github_coauthor_email", "")
    assert (
        _with_coauthor_trailer("lean-ai: test")
        == "lean-ai: test\n\nCo-authored-by: LeanAI-bot <leanai@timcomp.com>"
    )


def test_coauthor_trailer_appends_once(monkeypatch):
    monkeypatch.setattr(settings, "github_coauthor_enabled", True)
    monkeypatch.setattr(settings, "github_coauthor_name", "LeanAI-bot")
    monkeypatch.setattr(settings, "github_coauthor_email", "leanai@timcomp.com")

    first = _with_coauthor_trailer("lean-ai: test")
    second = _with_coauthor_trailer(first)

    assert second.count("Co-authored-by: LeanAI-bot <leanai@timcomp.com>") == 1
    assert second.endswith("Co-authored-by: LeanAI-bot <leanai@timcomp.com>")


@pytest.mark.asyncio
async def test_git_commit_files_does_not_include_pre_staged_user_changes(tmp_path, monkeypatch):
    """Scoped commits should not sweep unrelated staged changes into /init commits."""
    monkeypatch.setattr(settings, "github_coauthor_enabled", False)
    _init_repo(tmp_path)

    (tmp_path / "user.txt").write_text("user staged\n")
    _git(tmp_path, "add", "user.txt")
    (tmp_path / ".gitignore").write_text(".lean_ai/\n")

    result = await git_commit(
        "lean-ai: update gitignore",
        files=[".gitignore"],
        repo_root=str(tmp_path),
    )

    assert result.success, result.error or result.output
    committed_files = _git(tmp_path, "show", "--name-only", "--pretty=", "HEAD").stdout
    assert ".gitignore" in committed_files
    assert "user.txt" not in committed_files
    assert "A  user.txt" in _git(tmp_path, "status", "--porcelain").stdout


@pytest.mark.asyncio
async def test_git_stash_pop_restores_lean_ai_stash_not_latest_user_stash(tmp_path):
    """Recovery should not pop a newer user-created stash."""
    _init_repo(tmp_path)

    (tmp_path / "tracked.txt").write_text("lean-ai changes\n")
    assert await git_stash_push(str(tmp_path))

    (tmp_path / "tracked.txt").write_text("user changes\n")
    _git(tmp_path, "stash", "push", "-m", "user stash")

    result = await git_stash_pop(str(tmp_path))

    assert result.success, result.error or result.output
    assert (tmp_path / "tracked.txt").read_text() == "lean-ai changes\n"
    stash_list = _git(tmp_path, "stash", "list").stdout
    assert "user stash" in stash_list
    assert "lean-ai: auto-stash" not in stash_list
