"""Git operations via async subprocess."""

import asyncio
import logging

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult

logger = logging.getLogger(__name__)


async def _run_git(args: list[str], cwd: str) -> ToolResult:
    """Run a git command and capture output."""
    cmd = ["git"] + args
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.tool_timeout_seconds,
        )
        return ToolResult(
            success=process.returncode == 0,
            output=stdout.decode("utf-8", errors="replace"),
            error=stderr.decode("utf-8", errors="replace") if process.returncode != 0 else None,
            exit_code=process.returncode,
        )
    except asyncio.TimeoutError:
        return ToolResult(success=False, error="Git command timed out", exit_code=-1)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def git_commit(
    message: str, files: list[str] | None = None, repo_root: str = ".",
) -> ToolResult:
    """Stage files and commit."""
    if files:
        for f in files:
            result = await _run_git(["add", f], cwd=repo_root)
            if not result.success:
                return result
    else:
        result = await _run_git(["add", "-A"], cwd=repo_root)
        if not result.success:
            return result

    return await _run_git(["commit", "-m", message], cwd=repo_root)


async def git_status(repo_root: str = ".") -> ToolResult:
    return await _run_git(["status", "--porcelain"], cwd=repo_root)


async def git_diff(repo_root: str = ".", staged: bool = False) -> ToolResult:
    args = ["diff"]
    if staged:
        args.append("--cached")
    return await _run_git(args, cwd=repo_root)


async def git_current_branch(repo_root: str = ".") -> ToolResult:
    return await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)


async def git_current_sha(repo_root: str = ".") -> ToolResult:
    return await _run_git(["rev-parse", "HEAD"], cwd=repo_root)


async def git_is_repo(repo_root: str = ".") -> bool:
    """Check if the directory is inside a git repository."""
    result = await _run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_root)
    return result.success


async def git_create_branch(name: str, repo_root: str = ".") -> ToolResult:
    """Create and checkout a new branch."""
    return await _run_git(["checkout", "-b", name], cwd=repo_root)


async def git_checkout(name: str, repo_root: str = ".") -> ToolResult:
    """Checkout an existing branch."""
    return await _run_git(["checkout", name], cwd=repo_root)


async def git_merge_branch(name: str, repo_root: str = ".") -> ToolResult:
    """Merge a branch into the current branch."""
    return await _run_git(["merge", name], cwd=repo_root)


async def git_delete_branch(name: str, repo_root: str = ".", force: bool = False) -> ToolResult:
    """Delete a branch (-d or -D if force)."""
    flag = "-D" if force else "-d"
    return await _run_git(["branch", flag, name], cwd=repo_root)


async def git_stash_push(repo_root: str = ".") -> bool:
    """Stash uncommitted changes (including untracked). Returns True if anything was stashed."""
    # Check if there's anything to stash
    status = await git_status(repo_root)
    if not status.output.strip():
        return False
    result = await _run_git(
        ["stash", "push", "--include-untracked", "-m", "lean-ai: auto-stash"],
        cwd=repo_root,
    )
    return result.success


async def git_stash_pop(repo_root: str = ".") -> ToolResult:
    """Pop the latest stash."""
    return await _run_git(["stash", "pop"], cwd=repo_root)


async def git_add_and_commit(message: str, repo_root: str = ".") -> ToolResult:
    """Stage all changes and commit. Returns error if nothing to commit."""
    add_result = await _run_git(["add", "-A"], cwd=repo_root)
    if not add_result.success:
        return add_result
    # Check if there's anything staged
    status = await _run_git(["diff", "--cached", "--stat"], cwd=repo_root)
    if not status.output.strip():
        return ToolResult(success=True, output="Nothing to commit")
    return await _run_git(["commit", "-m", message], cwd=repo_root)
