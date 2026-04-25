"""Shell command runners: tests, lint, format, general-purpose."""

import logging
from pathlib import Path

from lean_ai.tools.executor import ToolResult
from lean_ai.tools.subprocess_utils import run_subprocess

logger = logging.getLogger(__name__)


async def _run_command(cmd: str, cwd: str) -> ToolResult:
    """Run a shell command and capture output."""
    return await run_subprocess(cmd, cwd)


async def run_tests(command: str, repo_root: str) -> ToolResult:
    """Run a test command."""
    return await _run_command(command, cwd=repo_root)


async def run_lint(command: str, repo_root: str) -> ToolResult:
    """Run a lint command."""
    return await _run_command(command, cwd=repo_root)


async def format_code(command: str, repo_root: str) -> ToolResult:
    """Run a code formatter."""
    return await _run_command(command, cwd=repo_root)


async def run_command(
    command: str,
    repo_root: str,
    working_directory: str = "",
) -> ToolResult:
    """Run a general-purpose shell command."""
    if working_directory:
        cwd = (Path(repo_root) / working_directory).resolve()
        if not cwd.is_relative_to(Path(repo_root).resolve()):
            return ToolResult(
                success=False,
                error=f"Working directory escapes repository root: {working_directory}",
            )
        if not cwd.is_dir():
            return ToolResult(
                success=False,
                error=f"Working directory does not exist: {working_directory}",
            )
        return await _run_command(command, cwd=str(cwd))
    return await _run_command(command, cwd=repo_root)
