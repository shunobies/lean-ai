"""Shared async subprocess execution.

Both :mod:`shell` (general-purpose commands) and :mod:`git_ops` use the
same timeout / error handling logic.  This module provides a single
implementation parameterized by shell vs exec mode and output handling.
"""

import asyncio
import logging

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult

logger = logging.getLogger(__name__)


async def run_subprocess(
    cmd: str | list[str],
    cwd: str,
    *,
    timeout: float | None = None,
    merge_stderr: bool = True,
) -> ToolResult:
    """Run a subprocess and capture output.

    Parameters
    ----------
    cmd:
        A string (interpreted by the shell) or list of args (exec mode).
    cwd:
        Working directory for the subprocess.
    timeout:
        Seconds before killing the process.  Defaults to
        ``settings.tool_timeout_seconds``.
    merge_stderr:
        When *True* (default), stdout and stderr are concatenated into
        ``output``.  When *False*, stderr is placed in the ``error``
        field only on non-zero exit.
    """
    if timeout is None:
        timeout = settings.tool_timeout_seconds

    try:
        if isinstance(cmd, str):
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        if merge_stderr:
            return ToolResult(
                success=process.returncode == 0,
                output=stdout_text + stderr_text,
                exit_code=process.returncode,
            )
        else:
            return ToolResult(
                success=process.returncode == 0,
                output=stdout_text,
                error=stderr_text if process.returncode != 0 else None,
                exit_code=process.returncode,
            )

    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass
        return ToolResult(
            success=False,
            error="Command timed out",
            exit_code=-1,
        )
    except Exception as e:
        return ToolResult(success=False, error=str(e))
