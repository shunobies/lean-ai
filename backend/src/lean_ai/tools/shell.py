"""Shell command runners: tests, lint, format."""

import asyncio
import logging

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult

logger = logging.getLogger(__name__)


async def _run_command(cmd: str, cwd: str) -> ToolResult:
    """Run a shell command and capture output."""
    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.tool_timeout_seconds,
        )
        return ToolResult(
            success=process.returncode == 0,
            output=stdout.decode("utf-8", errors="replace")
            + stderr.decode("utf-8", errors="replace"),
            exit_code=process.returncode,
        )
    except asyncio.TimeoutError:
        return ToolResult(success=False, error="Command timed out", exit_code=-1)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def run_tests(command: str, repo_root: str) -> ToolResult:
    """Run a test command."""
    return await _run_command(command, cwd=repo_root)


async def run_lint(command: str, repo_root: str) -> ToolResult:
    """Run a lint command."""
    return await _run_command(command, cwd=repo_root)


async def format_code(command: str, repo_root: str) -> ToolResult:
    """Run a code formatter."""
    return await _run_command(command, cwd=repo_root)
