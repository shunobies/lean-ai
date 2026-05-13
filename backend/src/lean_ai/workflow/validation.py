"""Post-execution validation and LLM-driven fix loop.

Extracted from pipeline.py for separation of concerns.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from lean_ai.config import settings
from lean_ai.workflow.ws_protocol import WorkflowSession
from lean_ai.llm.tool_definitions import (
    build_implementation_tools,
    build_tdd_implementation_tools,
)
from lean_ai.routers.context_helpers import load_condensed_context
from lean_ai.tools import scratchpad
from lean_ai.tools.journal import read_journal
from lean_ai.workflow.callbacks import build_workflow_callbacks
from lean_ai.workflow.prompts import build_fix_system_prompt
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)


# ── Deterministic Post-Execution Validation ───────────────────────


def _effective_post_commands(repo_root: str) -> dict[str, str]:
    """Resolve post-validation commands.

    Priority (highest → lowest):

    1. Per-project ``.lean_ai/commands.json`` fields written by
       ``/init-workspace`` auto-detection OR updated by the LLM
       during a plan that set up a new testing environment.
    2. Global ``LEAN_AI_POST_*`` env var settings — used as a
       fallback when commands.json is missing or has an empty
       field.

    The inversion from the historical "settings > auto" priority
    keeps per-project commands authoritative so switching between
    a Python repo and a PHP / Rust / Go repo no longer requires
    changing a global env var + restarting the server. When the
    env var is set but overridden by a per-project command, a
    debug log line announces the choice.

    Returns dict with keys: ``format``, ``lint_fix``, ``lint``,
    ``test``. Empty string for any command that has no source.
    """
    from lean_ai.context.command_detection import load_commands_json

    auto = load_commands_json(repo_root)
    env_overrides = {
        "format": settings.post_format_command,
        "lint_fix": settings.post_lint_fix_command,
        "lint": settings.post_lint_command,
        "test": settings.post_test_command,
    }
    resolved: dict[str, str] = {}
    for field, env_val in env_overrides.items():
        per_project = auto.get(field, "") or ""
        if per_project:
            resolved[field] = per_project
            if env_val and env_val != per_project:
                logger.debug(
                    "Post-validation %s: commands.json (%r) "
                    "overrides env var (%r) for this workspace",
                    field,
                    per_project,
                    env_val,
                )
        else:
            resolved[field] = env_val or ""
    return resolved


async def _run_post_validation(
    repo_root: str,
    ws: "WorkflowSession | None",
) -> dict:
    """Run deterministic post-execution lint, test, and auto-fix commands.

    Uses manually configured settings with auto-detected commands as
    fallback.  Auto-fix commands (format, lint-fix) run first and
    silently succeed; lint and test commands report pass/fail status
    via WebSocket.

    Returns a dict of results keyed by step name.
    """
    from lean_ai.tools import shell

    results: dict[str, dict] = {}
    commands = _effective_post_commands(repo_root)

    if not any(commands.values()):
        return results

    await ws_send(
        ws,
        "stage_status",
        {
            "stage": "post_validation",
            "status": "running",
            "summary": "Running post-execution validation...",
        },
    )

    # ── Auto-fix passes (silent success, report failure) ──
    for label, command, runner in [
        ("format", commands["format"], shell.format_code),
        ("lint_fix", commands["lint_fix"], shell.run_lint),
    ]:
        if not command:
            continue
        try:
            result = await runner(command=command, repo_root=repo_root)
            results[label] = {
                "success": result.success,
                "output": result.output[:2000] if result.output else "",
                "full_output": result.output or "",
            }
            if not result.success:
                logger.warning(
                    "Post-validation %s failed (exit %s): %s",
                    label,
                    result.exit_code,
                    result.output[:500],
                )
        except Exception as exc:
            logger.warning("Post-validation %s error: %s", label, exc)
            results[label] = {
                "success": False,
                "output": str(exc),
                "full_output": str(exc),
            }

    # ── Reporting passes (lint + test in parallel — both read-only) ──
    reporting_steps = [
        (label, cmd, runner)
        for label, cmd, runner in [
            ("lint", commands["lint"], shell.run_lint),
            ("test", commands["test"], shell.run_tests),
        ]
        if cmd
    ]

    if reporting_steps:

        async def _run_report(label, command, runner):
            try:
                result = await runner(command=command, repo_root=repo_root)
                return label, command, result, None
            except Exception as exc:
                return label, command, None, exc

        report_results = await asyncio.gather(
            *[_run_report(lbl, cmd, fn) for lbl, cmd, fn in reporting_steps],
        )

        for label, command, result, exc in report_results:
            if exc is not None:
                logger.warning("Post-validation %s error: %s", label, exc)
                results[label] = {
                    "success": False,
                    "output": str(exc),
                    "full_output": str(exc),
                }
                await ws_send(
                    ws,
                    "test_result",
                    {
                        "command": command,
                        "passed": False,
                        "output": str(exc),
                    },
                )
            else:
                results[label] = {
                    "success": result.success,
                    "output": result.output[:2000] if result.output else "",
                    "full_output": result.output or "",
                }
                await ws_send(
                    ws,
                    "test_result",
                    {
                        "command": command,
                        "passed": result.success,
                        "output": result.output[:2000] if result.output else "",
                    },
                )

    # ── Summary ──
    passed = sum(1 for r in results.values() if r["success"])
    total = len(results)
    summary = f"Post-validation: {passed}/{total} passed."
    if any(not r["success"] for r in results.values()):
        failed_names = [k for k, r in results.items() if not r["success"]]
        summary += f" Failed: {', '.join(failed_names)}."

    await ws_send(
        ws,
        "stage_status",
        {
            "stage": "post_validation",
            "status": "done",
            "summary": summary,
        },
    )

    logger.info("Post-validation complete: %s", summary)
    return results


# ── Validation-Resubmission Loop ──────────────────────────────────


async def _run_validation_fix_loop(
    repo_root: str,
    ws: "WorkflowSession | None",
    llm_client: "LLMClient",
    context: str,
    validation_results: dict,
    session_id: str = "",
    conversation_logger: Callable | None = None,
    expert_llm_client: "LLMClient | None" = None,
    dispatcher: WSMessageDispatcher | None = None,
    allowed_files: "list[str] | None" = None,
    task: str = "",
) -> dict:
    """Attempt to fix post-validation failures by resubmitting to the LLM.

    Runs up to ``post_validation_max_retries`` fix attempts.  Each attempt:

    1. Builds a focused fix prompt from failure output
    2. Runs ``chat_with_tools`` with a 30-turn budget
    3. Re-runs ``_run_post_validation`` (including auto-fix passes)

    When *allowed_files* is set, the tool executor restricts
    ``edit_file`` to only those paths (new file creation is still
    allowed).

    Returns the final validation results dict.
    """
    max_retries = settings.post_validation_max_retries
    if max_retries <= 0:
        return validation_results

    system_prompt = build_fix_system_prompt(
        load_condensed_context(repo_root),
        task=task,
    )

    # Callbacks — same WebSocket progress reporting used by the main loop
    cb = build_workflow_callbacks(
        ws,
        conversation_logger=conversation_logger,
        include_thinking=False,
    )

    attempts_used = 0
    for attempt in range(max_retries):
        # Check which validations failed
        failures = {k: v for k, v in validation_results.items() if not v["success"]}

        # Handle incomplete steps gracefully — log a warning but don't block
        incomplete_steps = [
            k for k, v in validation_results.items()
            if v.get("success") is None or (not v.get("output") and not v.get("full_output"))
        ]
        if incomplete_steps:
            logger.warning(
                "Incomplete validation steps detected (skipped, not blocking): %s",
                ", ".join(incomplete_steps),
            )

        if not failures:
            break  # All fixed

        attempts_used = attempt + 1

        # Escalate to expert model on final attempt
        is_final_attempt = attempt == max_retries - 1
        active_client = expert_llm_client if is_final_attempt and expert_llm_client else llm_client

        logger.info(
            "Validation fix attempt %d/%d: %d failure(s) — %s%s",
            attempts_used,
            max_retries,
            len(failures),
            ", ".join(failures.keys()),
            " (escalating to expert model)" if is_final_attempt and expert_llm_client else "",
        )

        escalation_note = ""
        if is_final_attempt and expert_llm_client:
            escalation_note = (
                f" (escalating to expert model: {expert_llm_client._provider.model_name})"
            )

        await ws_send(
            ws,
            "stage_status",
            {
                "stage": "validation_fix",
                "status": "running",
                "summary": (
                    f"Fix attempt {attempts_used}/{max_retries}: "
                    f"fixing {len(failures)} failure(s)..."
                    f"{escalation_note}"
                ),
            },
        )

        # Build focused fix prompt with full error output
        failure_parts: list[str] = []
        for name, result in failures.items():
            raw = result.get("full_output", result["output"])
            if len(raw) > 8000:
                # Tail is where errors are — keep last 80 lines
                lines = raw.splitlines()
                raw = "\n".join(lines[-80:])
            failure_parts.append(f"### {name}\n```\n{raw}\n```")
        failure_text = "\n\n".join(failure_parts)

        # Layer 7 — regression-aware banner. If any failing output
        # names a regression test file, prepend a bright warning so
        # the LLM knows the tool executor will reject edits to those
        # paths and the correct fix is an implementation change.
        from lean_ai.tools.regression_guard import (
            extract_regression_paths_from_text,
        )

        regression_hits: list[str] = []
        seen_reg: set[str] = set()
        for result in failures.values():
            raw = result.get("full_output") or result.get("output") or ""
            for p in extract_regression_paths_from_text(raw):
                if p not in seen_reg:
                    regression_hits.append(p)
                    seen_reg.add(p)
        regression_banner = ""
        if regression_hits:
            paths_list = ", ".join(f"`{p}`" for p in regression_hits)
            regression_banner = (
                "\n**REGRESSION TEST FAILURES DETECTED**: "
                f"{paths_list} are regression tests guarding "
                "previously-fixed or core behavior. They are "
                "IMMUTABLE. Your ONLY acceptable fix is to edit the "
                "implementation code so these tests pass again. The "
                "tool executor will REJECT any attempt to edit these "
                "files. Investigate why the recent changes broke the "
                "guarded behavior and restore it.\n"
            )

        fix_memory_context = ""
        if settings.enable_session_memory and getattr(
            settings,
            "enable_fix_loop_memory",
            True,
        ):
            from lean_ai.llm.planner_helpers import (
                retrieve_fix_pattern_memories,
            )

            # Query the memory index with a compact failure signature
            # (command names + first error line) so we retrieve past
            # fixes for similar failure shapes.
            memory_query_parts = list(failures.keys())
            for res in failures.values():
                raw = res.get("full_output") or res.get("output") or ""
                first_line = raw.strip().splitlines()[0] if raw.strip() else ""
                if first_line:
                    memory_query_parts.append(first_line[:200])
            fix_memory_context = await retrieve_fix_pattern_memories(
                repo_root,
                " ".join(memory_query_parts),
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Validation failed. Fix the failures below.\n"
                    "Workflow: re-run command → diagnose → fix → verify.\n"
                    + regression_banner
                    + (
                        "\nFILE SCOPE: Only modify files from this list "
                        "(new files are allowed):\n"
                        + "\n".join(f"- {f}" for f in allowed_files)
                        + "\n\n"
                        if allowed_files
                        else "\n"
                    )
                    + failure_text
                    + fix_memory_context
                ),
            },
        ]

        # Inject execution-phase state so the fix LLM has context
        if session_id:
            jrnl = read_journal(repo_root, session_id)
            if jrnl:
                messages.append(
                    {
                        "role": "user",
                        "content": f"[SESSION JOURNAL]\n{jrnl}",
                    }
                )
            pad = scratchpad.read_scratchpad(repo_root, session_id)
            if pad:
                messages.append(
                    {
                        "role": "user",
                        "content": f"[SCRATCHPAD]\n{pad}",
                    }
                )

        # Run LLM with a tight turn budget
        tdd_fix_protect = expert_llm_client is not None

        # Context refresh: rebuild messages from fresh disk state
        def _build_fix_refresh(current_messages: list[dict]) -> list[dict]:
            fresh_sys = build_fix_system_prompt(
                load_condensed_context(repo_root),
                task=task,
            )
            pad = scratchpad.read_scratchpad(repo_root, session_id)
            jrnl = read_journal(repo_root, session_id)
            refreshed: list[dict] = [
                {"role": "system", "content": fresh_sys},
                # Preserve the failure text (second message)
                {"role": "user", "content": current_messages[1]["content"]},
            ]
            if jrnl:
                refreshed.append(
                    {
                        "role": "user",
                        "content": f"[SESSION JOURNAL]\n{jrnl}",
                    }
                )
            if pad:
                refreshed.append(
                    {
                        "role": "user",
                        "content": f"[SCRATCHPAD]\n{pad}",
                    }
                )
            refreshed.append(
                {
                    "role": "user",
                    "content": "[CONTEXT REFRESHED] Continue fixing.",
                }
            )
            return refreshed

        def _build_validation_reminder(_ft=failure_text) -> str:
            parts = [
                "REMINDER — Fix validation failures:",
                _ft[:500],
            ]
            return "\n".join(parts)

        fix_tools = (
            build_tdd_implementation_tools() if tdd_fix_protect else build_implementation_tools()
        )
        validation_telemetry = {
            "repo_root": repo_root,
            "session_id": session_id,
            "phase": "validation_fix",
            "role": ("expert" if active_client is expert_llm_client else "primary"),
        }
        tool_executor = make_tool_executor(
            repo_root,
            ws,
            session_id,
            llm_client=active_client,
            dispatcher=dispatcher,
            tdd_protect_tests=tdd_fix_protect,
            allowed_files=allowed_files,
            telemetry_context=validation_telemetry,
        )
        executed, explanation = await active_client.chat_with_tools(
            messages=messages,
            tools=fix_tools,
            tool_executor_fn=tool_executor,
            max_turns=settings.post_validation_fix_turns,
            max_tokens=settings.implementation_max_tokens,
            task_reminder=_build_validation_reminder,
            reminder_interval=1,
            on_tool_call=cb.on_tool_call,
            on_tool_result=cb.on_tool_result,
            on_content=cb.on_content,
            on_metrics=cb.on_metrics,
            on_metrics_reset=cb.on_metrics_reset,
            on_context_refresh=_build_fix_refresh,
            dispatcher=dispatcher,
            telemetry_context=validation_telemetry,
        )

        logger.info(
            "Validation fix attempt %d: %d tool calls, re-validating...",
            attempts_used,
            len(executed),
        )

        # Re-run full validation (including auto-fix passes)
        failing_commands_before = list(failures.keys())
        error_snippet_before = failure_text[:3000]
        validation_results = await _run_post_validation(repo_root, ws)

        # Fire memory hook: if this attempt succeeded, extract a fix_pattern
        still_failing = {k: v for k, v in validation_results.items() if not v["success"]}
        attempt_succeeded = bool(failing_commands_before) and not still_failing
        try:
            from lean_ai.workflow.hooks import fire_validation_attempt_hook

            fix_tool_calls_payload = [
                {
                    "tool": tc.name,
                    "arguments": tc.arguments,
                }
                for tc in executed
            ]
            # Compact failure summaries for the training archive
            failures_before_summary = {
                name: (res.get("output") or "")[:500] for name, res in failures.items()
            }
            failures_after_summary = {
                name: (res.get("output") or "")[:500] for name, res in still_failing.items()
            }
            fire_validation_attempt_hook(
                repo_root=repo_root,
                session_id=session_id or "",
                llm_client=active_client,
                task=task or "",
                attempt_num=attempts_used,
                failing_commands=failing_commands_before,
                error_output=error_snippet_before,
                diagnosis=explanation or "",
                fix_tool_calls=fix_tool_calls_payload,
                succeeded=attempt_succeeded,
                failures_before=failures_before_summary,
                failures_after=failures_after_summary,
                ws=ws,
            )
        except Exception:
            logger.debug(
                "Validation attempt hook scheduling failed (non-fatal)",
                exc_info=True,
            )

    # Report final status
    final_failures = {k: v for k, v in validation_results.items() if not v["success"]}
    if attempts_used > 0:
        if final_failures:
            fix_summary = (
                f"Validation fix: {attempts_used} attempt(s), "
                f"{len(final_failures)} failure(s) remain: "
                f"{', '.join(final_failures.keys())}."
            )
        else:
            fix_summary = f"Validation fix: all issues resolved after {attempts_used} attempt(s)."

        await ws_send(
            ws,
            "stage_status",
            {
                "stage": "validation_fix",
                "status": "done",
                "summary": fix_summary,
            },
        )
        logger.info("Validation fix loop complete: %s", fix_summary)

    return validation_results
