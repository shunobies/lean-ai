"""Fix and request mode workflows — no planning, direct tool execution.

Extracted from pipeline.py for separation of concerns.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from lean_ai.config import settings
from lean_ai.llm.role_tuning import (
    ensure_expert_role_tuning,
    ensure_primary_role_tuning,
    ensure_request_role_tuning,
)
from lean_ai.llm.tool_definitions import (
    build_implementation_tools,
    build_investigation_tools,
    build_request_tools,
)
from lean_ai.routers.context_helpers import load_condensed_context
from lean_ai.tools import scratchpad
from lean_ai.tools.state_ledger import append_event, summarize_recent_events
from lean_ai.workflow.callbacks import build_workflow_callbacks
from lean_ai.workflow.prompts import (
    build_fix_investigation_prompt,
    build_fix_system_prompt,
    build_request_system_prompt,
)
from lean_ai.workflow.state import StateManager
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.validation import (
    _effective_post_commands,
    _run_post_validation,
    _run_validation_fix_loop,
)
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait
from lean_ai.workflow.ws_protocol import WorkflowSession

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)


_REFRESH_PAD_MAX_CHARS = 2000
_REFRESH_JOURNAL_MAX_CHARS = 1600
_INVESTIGATION_SUMMARY_MAX_CHARS = 2000


def read_journal(repo_root: str, session_id: str) -> str:
    """Compatibility helper for tests and legacy prompt-building code."""
    path = Path(repo_root) / ".lean_ai" / "journals" / f"{session_id}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _tail(text: str, max_chars: int) -> str:
    """Return the trailing slice of *text* capped at *max_chars*."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return "[TRUNCATED TO MOST RECENT CONTEXT]\n" + text[-max_chars:]


async def _run_fix(
    task: str,
    repo_root: str,
    ws: "WorkflowSession | None",
    llm_client: "LLMClient",
    context: str,
    branch_name: str,
    base_branch: str = "",
    session_id: str = "",
    conversation_logger: Callable | None = None,
    state_manager: StateManager = None,
    expert_llm_client: "LLMClient | None" = None,
    request_llm_client: "LLMClient | None" = None,
    mode: str = "fix",
    dispatcher: WSMessageDispatcher | None = None,
) -> str:
    """Execute directly — no planning, no approval.

    The LLM gets the full tool set and runs until it decides it's done.
    When *mode* is ``"request"``, uses a neutral prompt and optionally
    a separate request model.
    """
    if state_manager is None:
        state_manager = StateManager(session_id or "fix")
    bootstrap_state = await state_manager.get_state_async()
    if bootstrap_state.session_id:
        if not bootstrap_state.scratchpad_content:
            bootstrap_state.scratchpad_content = scratchpad.read_scratchpad(
                repo_root,
                bootstrap_state.session_id,
            )
        if not bootstrap_state.journal_entries:
            journal_text = read_journal(repo_root, bootstrap_state.session_id)
            if journal_text:
                bootstrap_state.journal_entries = [
                    line.strip() for line in journal_text.splitlines() if line.strip()
                ]
    if dispatcher:
        dispatcher.enter_execution_mode()
    append_event(
        repo_root=repo_root,
        session_id=state_manager.session_id,
        event_type="phase_transition",
        payload={"phase": mode},
    )

    is_request = mode == "request"
    # Use dedicated model when available: request model for /request,
    # expert model for /fix (bug diagnosis is reasoning-heavy).
    if is_request:
        active_client = request_llm_client or llm_client
    else:
        active_client = expert_llm_client or llm_client
    fix_role = (
        "request" if is_request else ("expert" if expert_llm_client is not None else "primary")
    )
    fix_telemetry = {
        "repo_root": repo_root,
        "session_id": state_manager.session_id,
        "phase": mode,
        "role": fix_role,
    }
    tool_executor = make_tool_executor(
        repo_root,
        ws,
        session_id=state_manager.session_id,
        llm_client=active_client,
        dispatcher=dispatcher,
        telemetry_context=fix_telemetry,
    )
    commands = _effective_post_commands(repo_root)
    execution_context = load_condensed_context(repo_root)
    prompt_scope = None
    if is_request:
        prompt_scope = await ensure_request_role_tuning(
            repo_root=repo_root,
            assigned_client=active_client,
            primary_client=llm_client,
            expert_client=expert_llm_client,
        )
        system_prompt = build_request_system_prompt(
            execution_context,
            repo_root=repo_root,
            prompt_scope=prompt_scope,
        )
    else:
        if active_client is expert_llm_client and expert_llm_client is not None:
            prompt_scope = await ensure_expert_role_tuning(
                repo_root=repo_root,
                assigned_client=active_client,
                primary_client=llm_client,
                expert_client=expert_llm_client,
            )
        else:
            prompt_scope = await ensure_primary_role_tuning(
                repo_root=repo_root,
                assigned_client=active_client,
                primary_client=llm_client,
                expert_client=expert_llm_client,
            )
        system_prompt = build_fix_system_prompt(
            execution_context,
            test_command=commands.get("test", ""),
            task=task,
            repo_root=repo_root,
            prompt_scope=prompt_scope,
        )

    # Callbacks — fire-and-forget (same rationale as plan mode callbacks)
    cb = build_workflow_callbacks(
        ws,
        conversation_logger=conversation_logger,
    )

    def _build_implementation_messages(
        *,
        investigation_summary: str = "",
    ) -> list[dict]:
        """Create a fresh implementation prompt rooted in durable state."""
        new_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

        state = state_manager.get_state()

        if state.session_id:
            existing_journal = "\n".join(state.journal_entries) if state.journal_entries else ""
            if existing_journal:
                new_messages.append(
                    {
                        "role": "user",
                        "content": (f"[JOURNAL FROM PREVIOUS EXECUTION]\n{existing_journal}"),
                    }
                )
            existing_pad = state.scratchpad_content
            if existing_pad:
                new_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[SCRATCHPAD FROM PREVIOUS EXECUTION — resume from here]\n"
                            f"{existing_pad}"
                        ),
                    }
                )

        investigation_summary = investigation_summary.strip()
        if investigation_summary:
            if len(investigation_summary) > _INVESTIGATION_SUMMARY_MAX_CHARS:
                investigation_summary = (
                    investigation_summary[:_INVESTIGATION_SUMMARY_MAX_CHARS]
                    + "\n[TRUNCATED INVESTIGATION SUMMARY]"
                )
            new_messages.append(
                {
                    "role": "user",
                    "content": (
                        "[INVESTIGATION HANDOFF]\n"
                        "Treat the journal and scratchpad as the source of truth. "
                        "Use this summary only as a compact reminder of what the "
                        "investigation phase found:\n"
                        f"{investigation_summary}"
                    ),
                }
            )

        new_messages.append(
            {
                "role": "user",
                "content": (
                    "MODE: IMPLEMENTATION\n"
                    "All tools now available: create_file, edit_file, "
                    "run_command, format_code (plus all investigation tools).\n"
                    "Use your scratchpad diagnosis. Make the minimal fix. "
                    "Do not continue investigating."
                ),
            }
        )
        return new_messages

    # ── Investigation phase (fix mode only) ───────────────────────
    # Run read-only tools first so the LLM gathers context before editing.
    executed_investigation: list = []
    investigation_summary = ""
    if not is_request and settings.enable_fix_investigation:
        await ws_send(ws, "stage_change", {"stage": "investigating"})

        investigation_prompt = build_fix_investigation_prompt(
            execution_context,
            test_command=commands.get("test", ""),
        )

        messages = [
            {"role": "system", "content": investigation_prompt},
            {"role": "user", "content": task},
        ]

        # Inject existing journal + scratchpad for session recovery
        if state_manager.session_id:
            inv_state = state_manager.get_state()
            existing_journal = (
                "\n".join(inv_state.journal_entries) if inv_state.journal_entries else ""
            )
            if existing_journal:
                messages.append(
                    {
                        "role": "user",
                        "content": (f"[JOURNAL FROM PREVIOUS EXECUTION]\n{existing_journal}"),
                    }
                )
            existing_pad = inv_state.scratchpad_content
            if existing_pad:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[SCRATCHPAD FROM PREVIOUS EXECUTION — resume from here]\n"
                            f"{existing_pad}"
                        ),
                    }
                )

        def _build_investigation_refresh(
            current_messages: list[dict],
        ) -> list[dict]:
            """Rebuild investigation messages from fresh disk state."""
            fresh_context = load_condensed_context(repo_root)
            fresh_prompt = build_fix_investigation_prompt(
                fresh_context,
                test_command=commands.get("test", ""),
            )
            refresh_state = state_manager.get_state()
            pad = refresh_state.scratchpad_content
            jrnl = "\n".join(refresh_state.journal_entries) if refresh_state.journal_entries else ""
            refreshed: list[dict] = [
                {"role": "system", "content": fresh_prompt},
                {"role": "user", "content": task},
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
                    "content": ("[CONTEXT REFRESHED] Continue investigating."),
                }
            )
            return refreshed

        investigation_telemetry = dict(fix_telemetry)
        investigation_telemetry["phase"] = "fix.investigate"
        executed_investigation, investigation_summary = await active_client.chat_with_tools(
            messages=messages,
            tools=build_investigation_tools(),
            tool_executor_fn=tool_executor,
            max_turns=20,
            max_tokens=settings.implementation_max_tokens,
            on_tool_call=cb.on_tool_call,
            on_tool_result=cb.on_tool_result,
            on_content=cb.on_content,
            on_thinking=cb.on_thinking,
            on_metrics=cb.on_metrics,
            on_metrics_reset=cb.on_metrics_reset,
            on_context_refresh=_build_investigation_refresh,
            dispatcher=dispatcher,
            telemetry_context=investigation_telemetry,
        )

        # Transition: start implementation from a fresh prompt root so
        # context budgets do not inherit the full investigation transcript.
        messages = _build_implementation_messages(
            investigation_summary=investigation_summary,
        )
    else:
        messages = _build_implementation_messages()

    def _build_fix_reminder() -> str:
        parts = [f"REMINDER — Your task: {task}"]
        reminder_state = state_manager.get_state()
        jrnl = "\n".join(reminder_state.journal_entries) if reminder_state.journal_entries else ""
        if jrnl:
            parts.append(f"\nYour session journal:\n{jrnl}")
        pad = reminder_state.scratchpad_content
        if pad:
            parts.append(f"\nYour current scratchpad:\n{pad}")
        else:
            parts.append("\nCall update_scratchpad to record your progress so far.")
        return "\n".join(parts)

    def _build_context_refresh(current_messages: list[dict]) -> list[dict]:
        """Rebuild message list from fresh disk state."""
        fresh_context = load_condensed_context(repo_root)
        if is_request:
            fresh_system_prompt = build_request_system_prompt(
                fresh_context,
                repo_root=repo_root,
                prompt_scope=prompt_scope,
            )
        else:
            fresh_system_prompt = build_fix_system_prompt(
                fresh_context,
                test_command=commands.get("test", ""),
                task=task,
                repo_root=repo_root,
                prompt_scope=prompt_scope,
            )
        refresh_state = state_manager.get_state()
        pad = refresh_state.scratchpad_content
        jrnl = "\n".join(refresh_state.journal_entries) if refresh_state.journal_entries else ""

        new_messages: list[dict] = [
            {"role": "system", "content": fresh_system_prompt},
            {"role": "user", "content": task},
        ]
        refresh_parts = ["[CONTEXT REFRESHED]"]
        if jrnl:
            refresh_parts.append(
                "SESSION JOURNAL (recent permanent findings):\n"
                f"{_tail(jrnl, _REFRESH_JOURNAL_MAX_CHARS)}"
            )
        if pad:
            refresh_parts.append(
                f"SCRATCHPAD (recent current state):\n{_tail(pad, _REFRESH_PAD_MAX_CHARS)}"
            )
        if pad or jrnl:
            new_messages.append(
                {
                    "role": "user",
                    "content": "\n\n".join(refresh_parts),
                }
            )
        else:
            new_messages.append(
                {
                    "role": "user",
                    "content": "[CONTEXT REFRESHED]\n\nContinue working on the task.",
                }
            )
        ledger_summary = summarize_recent_events(repo_root, state_manager.session_id)
        if ledger_summary:
            new_messages.append(
                {
                    "role": "user",
                    "content": f"RECENT STATE LEDGER (machine events):\n{ledger_summary}",
                }
            )
        append_event(
            repo_root=repo_root,
            session_id=state_manager.session_id,
            event_type="context_refreshed",
            payload={"phase": mode},
        )

        ws_send_nowait(
            ws,
            "context_refreshed",
            {
                "message": "Context refreshed — journal and scratchpad provide continuity.",
            },
        )
        return new_messages

    # Request mode: stronger nudge that suggests specific tools for open-ended tasks
    request_nudge = (
        ("Call one tool now. Prefer reading workspace state before broader research.")
        if is_request
        else None
    )

    # ── Implementation phase ──────────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "implementing"})

    executed, explanation = await active_client.chat_with_tools(
        messages=messages,
        tools=(build_request_tools() if is_request else build_implementation_tools()),
        tool_executor_fn=tool_executor,
        max_turns=settings.implementation_max_turns,
        max_tokens=settings.implementation_max_tokens,
        task_reminder=_build_fix_reminder,
        reminder_interval=settings.reminder_interval,
        text_only_nudge=request_nudge,
        on_tool_call=cb.on_tool_call,
        on_tool_result=cb.on_tool_result,
        on_content=cb.on_content,
        on_thinking=cb.on_thinking,
        on_metrics=cb.on_metrics,
        on_metrics_reset=cb.on_metrics_reset,
        on_context_refresh=_build_context_refresh,
        dispatcher=dispatcher,
        telemetry_context=fix_telemetry,
    )

    # ── Completion ────────────────────────────────────────────────
    files_modified = list(
        {
            tc.parameters.get("path", "")
            for tc in executed
            if tc.tool_name in ("create_file", "edit_file") and tc.parameters.get("path")
        }
    )

    # ── Post-execution validation ──
    validation_results: dict = {}
    if files_modified and settings.enable_post_validation:
        validation_results = await _run_post_validation(repo_root, ws)

        # Attempt to fix validation failures via LLM
        if (
            validation_results
            and any(not r["success"] for r in validation_results.values())
            and settings.post_validation_max_retries > 0
        ):
            validation_results = await _run_validation_fix_loop(
                repo_root,
                ws,
                llm_client,
                context,
                validation_results,
                state_manager,
                conversation_logger=conversation_logger,
                expert_llm_client=expert_llm_client,
                dispatcher=dispatcher,
                allowed_files=files_modified,
                task=task,
            )

    all_executed = executed_investigation + executed
    summary = (
        f"Fix complete: {len(all_executed)} tool calls. "
        f"Files modified: {', '.join(files_modified) if files_modified else 'none'}."
    )
    if explanation.strip():
        summary += f"\n\n{explanation.strip()}"
    if validation_results:
        failed = {k: r for k, r in validation_results.items() if not r["success"]}
        if failed:
            summary += "\n\n⚠️ Post-validation failures:"
            for name, result in failed.items():
                summary += f"\n  {name}: {result['output'][:200]}"
        else:
            summary += "\n\n✓ Post-validation passed."

    final_state = state_manager.get_state()
    journal_content = "\n".join(final_state.journal_entries) if final_state.journal_entries else ""
    if journal_content:
        summary += f"\n\nSession Journal:\n{journal_content}"

    # ── Incremental project_context.md update ──
    if files_modified and settings.enable_project_context:
        await ws_send(
            ws,
            "stage_status",
            {
                "stage": "context_update",
                "status": "running",
                "summary": f"Updating project context with {len(files_modified)} modified files...",
            },
        )
        try:
            from lean_ai.context.generation import update_project_context
            from lean_ai.routers.dependencies import worker_llm_client

            ctx_path = await update_project_context(
                repo_root,
                files_modified,
                llm_client,
                worker_client=worker_llm_client,
            )
            if ctx_path:
                logger.info(
                    "project_context.md updated with %d modified files",
                    len(files_modified),
                )
                await ws_send(
                    ws,
                    "stage_status",
                    {
                        "stage": "context_update",
                        "status": "done",
                        "summary": "Project context updated.",
                    },
                )
            else:
                logger.info("project_context.md update skipped (no changes needed)")
                await ws_send(
                    ws,
                    "stage_status",
                    {
                        "stage": "context_update",
                        "status": "done",
                        "summary": "Project context update skipped (no changes needed).",
                    },
                )
        except Exception as exc:
            logger.warning("Incremental context update failed (non-fatal): %s", exc)
            await ws_send(
                ws,
                "stage_status",
                {
                    "stage": "context_update",
                    "status": "done",
                    "summary": f"Project context update failed: {exc}",
                },
            )

    complete_data: dict = {"summary": summary, "files_modified": files_modified}
    if branch_name:
        complete_data["plan_branch"] = branch_name
    if base_branch:
        complete_data["base_branch"] = base_branch
    await ws_send(ws, "complete", complete_data)
    logger.info(
        "Fix complete: %d tool calls, %d files",
        len(all_executed),
        len(files_modified),
    )

    task_summary = task[:72].replace("\n", " ")
    prefix = "lean-ai(request)" if is_request else "lean-ai(fix)"
    commit_msg = f"{prefix}: {task_summary}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg
