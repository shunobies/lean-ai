"""Tests for the ``POST /workspace/log-applied`` endpoint.

Covers the deterministic application-log flow used by the
``/log-applied`` extension command: append a row to ``applications.md``
and commit the per-application folder when git is available.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from lean_ai.main import app

    return TestClient(app)


def _seed_application(repo_root: Path, slug: str) -> None:
    """Create a minimal ``applications/{slug}/`` folder with one artefact."""
    app_dir = repo_root / "applications" / slug
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "resume.md").write_text("# Example resume\n", encoding="utf-8")


def _seed_tracker(repo_root: Path) -> Path:
    """Write a minimal ``applications.md`` with the expected table header."""
    tracker = repo_root / "applications.md"
    header = (
        "| Date | Company | Role | Source | Status | Last Contact | "
        "Next Action | Folder |"
    )
    sep = "|------|---------|------|--------|--------|--------------|-------------|--------|"
    seed_row = (
        "| 2026-04-01 | Example Corp | Senior Engineer | LinkedIn | applied | — | — | "
        "`applications/example_corp_senior_engineer/` |"
    )
    tracker.write_text(
        "# Applications Tracker\n\n"
        f"{header}\n{sep}\n{seed_row}\n",
        encoding="utf-8",
    )
    return tracker


def _init_git_repo(repo_root: Path) -> None:
    """Initialise a git repo inside *repo_root* with an initial commit."""
    subprocess.run(
        ["git", "-C", str(repo_root), "init", "-q", "-b", "main"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    # Seed a commit so HEAD exists.
    (repo_root / "README.md").write_text("# seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "README.md"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-q", "-m", "seed"],
        check=True, capture_output=True,
    )


# ── success cases ────────────────────────────────────────────────────


def test_log_applied_updates_tracker_and_commits_in_git_repo(
    tmp_path: Path, client,
) -> None:
    _seed_application(tmp_path, "acme_corp_senior_engineer")
    _seed_tracker(tmp_path)
    _init_git_repo(tmp_path)

    resp = client.post(
        "/api/workspace/log-applied",
        json={
            "repo_root": str(tmp_path),
            "slug": "acme_corp_senior_engineer",
            "company": "Acme Corp",
            "role": "Senior Engineer",
            "source": "LinkedIn",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["tracker_updated"] is True
    assert data["commit_sha"] is not None
    assert data["commit_error"] is None

    tracker = (tmp_path / "applications.md").read_text(encoding="utf-8")
    assert "Acme Corp" in tracker
    assert "LinkedIn" in tracker
    assert "applications/acme_corp_senior_engineer/" in tracker

    # The commit message should name the company/role.
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "-1", "--pretty=%s"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert log == "Applied: Acme Corp — Senior Engineer"


def test_log_applied_without_git_still_updates_tracker(
    tmp_path: Path, client,
) -> None:
    _seed_application(tmp_path, "slug1")
    _seed_tracker(tmp_path)
    # No _init_git_repo call.

    resp = client.post(
        "/api/workspace/log-applied",
        json={
            "repo_root": str(tmp_path),
            "slug": "slug1",
            "company": "TestCo",
            "role": "Engineer",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tracker_updated"] is True
    assert data["commit_sha"] is None
    assert data["commit_error"] == "not a git repository"

    tracker = (tmp_path / "applications.md").read_text(encoding="utf-8")
    assert "TestCo" in tracker


def test_log_applied_without_tracker_skips_tracker_update(
    tmp_path: Path, client,
) -> None:
    _seed_application(tmp_path, "slug2")
    _init_git_repo(tmp_path)
    # No tracker file.

    resp = client.post(
        "/api/workspace/log-applied",
        json={
            "repo_root": str(tmp_path),
            "slug": "slug2",
            "company": "TestCo",
            "role": "Engineer",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tracker_updated"] is False
    assert data["tracker_path"] is None
    # The commit should still happen over the newly-added applications/slug2/
    assert data["commit_sha"] is not None


def test_log_applied_appends_row_after_last_existing_row(
    tmp_path: Path, client,
) -> None:
    """Rows land after the last table row, not after trailing prose."""
    _seed_application(tmp_path, "slug3")
    tracker = tmp_path / "applications.md"
    tracker.write_text(
        "# Tracker\n\n"
        "| Date | Company | Role |\n"
        "|------|---------|------|\n"
        "| 2026-04-01 | OldCo | Eng |\n"
        "\n"
        "Notes below the table — must stay below the new row.\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/api/workspace/log-applied",
        json={
            "repo_root": str(tmp_path),
            "slug": "slug3",
            "company": "NewCo",
            "role": "SRE",
        },
    )
    assert resp.status_code == 200

    body = tracker.read_text(encoding="utf-8").splitlines()
    newco_idx = next(i for i, line in enumerate(body) if "NewCo" in line)
    notes_idx = next(i for i, line in enumerate(body) if "Notes below the table" in line)
    assert newco_idx < notes_idx, "new row inserted after trailing prose"


# ── error cases ──────────────────────────────────────────────────────


def test_log_applied_missing_slug_returns_404(tmp_path: Path, client) -> None:
    _seed_tracker(tmp_path)

    resp = client.post(
        "/api/workspace/log-applied",
        json={
            "repo_root": str(tmp_path),
            "slug": "does_not_exist",
            "company": "TestCo",
            "role": "Engineer",
        },
    )
    assert resp.status_code == 404
    assert "applications/does_not_exist/" in resp.json()["detail"]


def test_log_applied_rejects_slug_with_slash(tmp_path: Path, client) -> None:
    resp = client.post(
        "/api/workspace/log-applied",
        json={
            "repo_root": str(tmp_path),
            "slug": "../escape",
            "company": "TestCo",
            "role": "Engineer",
        },
    )
    assert resp.status_code == 400


def test_log_applied_rejects_missing_repo_root(tmp_path: Path, client) -> None:
    resp = client.post(
        "/api/workspace/log-applied",
        json={
            "repo_root": str(tmp_path / "nope"),
            "slug": "slug",
            "company": "TestCo",
            "role": "Engineer",
        },
    )
    assert resp.status_code == 400
