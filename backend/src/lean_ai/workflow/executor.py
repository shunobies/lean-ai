"""Plan step execution engine: sequential and parallel step execution.

Handles both normal execution and TDD three-phase execution
(expert writes tests → primary reviews → primary implements).
"""

import asyncio
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from lean_ai.config import settings
from lean_ai.workflow.ws_protocol import WorkflowSession
from lean_ai.context.metadata import invalidate_metadata_cache_for_paths
from lean_ai.indexer.tree import list_repo_tree
from lean_ai.llm.base import ToolCall
from lean_ai.llm.plan_schema import (
    ExecutionPlan,
    PlanStep,
    format_name_registry_for_prompt,
    format_naming_conventions_for_prompt,
    plan_to_markdown,
)
from lean_ai.llm.tool_definitions import (
    build_implementation_tools,
    build_tdd_implementation_tools,
)
from lean_ai.routers.context_helpers import load_execution_context
from lean_ai.tools import scratchpad
from lean_ai.tools.journal import read_journal
from lean_ai.workflow.callbacks import build_workflow_callbacks
from lean_ai.workflow.hooks import (
    auto_extract_session_memories,
    auto_push_integration,
)
from lean_ai.workflow.prompts import (
    build_step_system_prompt,
    build_step_user_message,
    build_tdd_step_system_prompt,
    build_tdd_test_writing_prompt,
)
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.validation import (
    _run_post_validation,
    _run_validation_fix_loop,
)
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)

# Tools that act as barriers — they depend on ALL prior steps completing.
_BARRIER_TOOLS = frozenset(
    {
        "run_tests",
        "run_lint",
        "format_code",
        "run_command",
    }
)
_FILE_WRITE_TOOLS = frozenset({"create_file", "edit_file"})
_IMPLICIT_MUTATION_TOOLS = frozenset({"run_command", "format_code"})
_MUTATION_TOOLS = _FILE_WRITE_TOOLS | _IMPLICIT_MUTATION_TOOLS
_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "list_directory",
        "directory_tree",
        "grep_files",
        "query_project_context",
        "search_reference",
        "search_wiki",
        "fetch_wiki_page",
        "search_internet",
        "fetch_url",
        "update_scratchpad",
        "add_journal_entry",
    }
)
_COMPLETION_TOOL = "task_complete"
_INCOMPLETE_REL_PATH = ".lean_ai/incomplete.md"
_TDD_MAX_RETRIES = 2


def _collect_step_test_commands(step: PlanStep) -> list[str]:
    """Return test commands from a step's success_checks.

    Inspects ``success_checks`` for entries whose tool is ``run_tests``
    and returns the associated command strings.  Empty when the step
    declares no test-based success checks.
    """
    commands: list[str] = []
    for check in getattr(step, "success_checks", None) or []:
        if check.tool == "run_tests" and check.command:
            commands.append(check.command)
    return commands


async def _run_step_tests(
    repo_root: str,
    test_commands: list[str],
) -> tuple[bool, str]:
    """Run one or more test commands and return (all_passed, combined_output).

    Returns ``True`` only when every command exits successfully.
    """
    from lean_ai.tools.shell import run_tests as shell_run_tests

    outputs: list[str] = []
    for cmd in test_commands:
        result = await shell_run_tests(cmd, repo_root)
        outputs.append(str(result))
        if not result.success:
            return False, "\n".join(outputs)
    return True, "\n".join(outputs)


def _normalize_path(p: str) -> str:
    """Strip leading ``./`` for consistent path comparison."""
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_mentioned_in(fpath: str, text: str) -> bool:
    """Check if *fpath* is explicitly referenced in *text*.

    Uses boundary-aware matching so that ``a.py`` does not falsely
    match ``baa.py`` or other longer strings.
    """
    if not fpath or not text:
        return False
    escaped = re.escape(_normalize_path(fpath))
    # Boundary: start-of-string or common delimiters.
    # Trailing boundary excludes '.' to prevent config.py matching config.py.bak
    pattern = r"(?:^|[\s`\"'(,\[])" + escaped + r"(?:[\s`\"')\],;:\n]|$)"
    return bool(re.search(pattern, text))


def _tool_result_failed(result: str) -> bool:
    """Return True when a tool result represents an execution failure."""
    if not isinstance(result, str):
        return False
    return result.lstrip().upper().startswith(("ERROR:", "FAILED"))


def _step_allowed_tool_names(step: PlanStep) -> set[str]:
    allowed = {name for name in (getattr(step, "allowed_tools", None) or []) if name}
    if getattr(step, "tool", ""):
        allowed.add(step.tool)
    allowed.add(_COMPLETION_TOOL)
    return allowed


def _step_may_change_paths(step: PlanStep) -> set[str]:
    paths = {
        _normalize_path(target.path)
        for target in (getattr(step, "may_change", None) or [])
        if getattr(target, "path", "").strip()
    }
    if getattr(step, "file_path", "") and getattr(step, "tool", "") in _MUTATION_TOOLS:
        paths.add(_normalize_path(step.file_path))
    return paths


def _step_success_check_text(step: PlanStep) -> str:
    parts: list[str] = []
    for check in getattr(step, "success_checks", None) or []:
        parts.append(check.description or "")
        parts.append(check.tool or "")
        parts.append(check.command or "")
        parts.append(check.expected or "")
    return " ".join(part for part in parts if part)


def _step_primary_label(step: PlanStep) -> str:
    job = getattr(step, "job", "") or getattr(step, "instruction", "") or "planned job"
    paths = sorted(_step_may_change_paths(step))
    if paths:
        return f"{job} ({', '.join(paths)})"
    return job


def _step_scope_error(
    step: PlanStep,
    tool_name: str,
    arguments: dict,
) -> str | None:
    """Return an executor-side scope violation for the current step, if any.

    The ``allowed_tools`` list in the plan is advisory metadata during execution
    and does not block tool usage.  Path-based ``may_change`` boundaries are still
    enforced for ``create_file`` and ``edit_file`` to keep mutations within the
    planned scope.
    """
    if tool_name in _FILE_WRITE_TOOLS:
        target_path = _normalize_path(arguments.get("path", ""))
        allowed_paths = _step_may_change_paths(step)
        if not allowed_paths:
            return (
                "ERROR: This step does not declare any `may_change` file targets, "
                f"so `{tool_name}` cannot safely write `{arguments.get('path', '')}`."
            )
        if target_path not in allowed_paths:
            return (
                f"ERROR: This step may only modify: {', '.join(sorted(allowed_paths))}. "
                f"You tried to modify `{arguments.get('path', '')}`. "
                "Stay inside the planned mutation boundary for this step."
            )
        return None

    return None


def _step_primary_action_done(
    step: PlanStep,
    *,
    successful_calls: list[ToolCall],
    attempted_calls: list[ToolCall],
) -> bool:
    """Return True when the step's bounded job appears to have happened."""
    allowed_paths = _step_may_change_paths(step)
    allowed_tools = _step_allowed_tool_names(step)

    if not allowed_paths and step.tool == "read_file":
        return any(
            tc.tool_name == "read_file"
            and (
                not step.file_path
                or _normalize_path(tc.parameters.get("path", "")) == _normalize_path(step.file_path)
            )
            for tc in successful_calls
        )

    if allowed_paths:
        return any(
            tc.tool_name in _FILE_WRITE_TOOLS
            and _normalize_path(tc.parameters.get("path", "")) in allowed_paths
            for tc in successful_calls
        ) or any(tc.tool_name in _IMPLICIT_MUTATION_TOOLS for tc in successful_calls)

    command_checks = [
        check
        for check in getattr(step, "success_checks", None) or []
        if check.tool in _BARRIER_TOOLS or check.tool == "run_command"
    ]
    if command_checks:
        return all(
            any(
                tc.tool_name == check.tool
                and (
                    not check.command
                    or tc.parameters.get("command", "").strip() == check.command.strip()
                )
                for tc in attempted_calls
            )
            for check in command_checks
        )

    mutating_allowed = allowed_tools & _MUTATION_TOOLS
    if mutating_allowed:
        return any(tc.tool_name in mutating_allowed for tc in successful_calls)

    return True


def _step_completion_error(
    step: PlanStep,
    *,
    task_complete_seen: bool,
    successful_calls: list[ToolCall],
    attempted_calls: list[ToolCall],
) -> str | None:
    """Return a human-readable reason the step is still incomplete."""
    if not _step_primary_action_done(
        step,
        successful_calls=successful_calls,
        attempted_calls=attempted_calls,
    ):
        return f"Step never completed its required job action: {_step_primary_label(step)}."

    for check in getattr(step, "success_checks", None) or []:
        if not check.tool or check.tool not in _BARRIER_TOOLS | {"run_command"}:
            continue
        matched = any(
            tc.tool_name == check.tool
            and (
                not check.command
                or tc.parameters.get("command", "").strip() == check.command.strip()
            )
            for tc in attempted_calls
        )
        if not matched:
            command = f" `{check.command}`" if check.command else ""
            return (
                f"Step did not run required success check `{check.tool}`{command}: "
                f"{check.description}"
            )

    if not task_complete_seen:
        return (
            "Step ended without task_complete, so the executor cannot safely treat it as finished."
        )

    return None


def _clear_incomplete_file(repo_root: str) -> None:
    """Remove stale incomplete state from a previous execution run."""
    incomplete_path = Path(repo_root) / _INCOMPLETE_REL_PATH
    try:
        incomplete_path.unlink(missing_ok=True)
    except Exception:
        logger.debug("Failed to clear stale incomplete.md", exc_info=True)


def _append_incomplete_entry(
    repo_root: str,
    *,
    step_label: str,
    detail: str,
) -> None:
    """Append one current-run failure entry to ``.lean_ai/incomplete.md``."""
    incomplete_path = Path(repo_root) / _INCOMPLETE_REL_PATH
    incomplete_path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"## {step_label}\n{detail.strip()}\n\n"
    with incomplete_path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def _snapshot_repo_state(repo_root: str) -> dict[str, tuple[int, int]]:
    """Capture a gitignore-aware text-file snapshot of the repository."""
    root = Path(repo_root)
    snapshot: dict[str, tuple[int, int]] = {}
    for entry in list_repo_tree(repo_root):
        full_path = root / entry.path
        try:
            stat = full_path.stat()
        except OSError:
            continue
        snapshot[entry.path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _diff_repo_state(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> list[str]:
    """Return repo-relative text-file paths added/changed/deleted between snapshots."""
    changed: set[str] = set()
    for path, meta in after.items():
        if before.get(path) != meta:
            changed.add(path)
    for path in before:
        if path not in after:
            changed.add(path)
    return sorted(changed)


def _collect_tdd_review_files(steps: list[PlanStep]) -> list[str]:
    """Return all changed test-file paths that should enter TDD review."""
    from lean_ai.tools.test_file_utils import is_test_file_path

    review_files: list[str] = []
    seen: set[str] = set()
    for step in steps:
        if not step.file_path:
            continue
        if not is_test_file_path(step.file_path):
            continue
        if step.file_path in seen:
            continue
        seen.add(step.file_path)
        review_files.append(step.file_path)
    return review_files


def _build_step_groups(
    steps: list[PlanStep],
) -> list[list[PlanStep]]:
    """Group plan steps by dependency for parallel execution.

    Rules:
    - Same ``file_path`` → sequential (same group boundary).
    - Step B's structured job contract mentions step A's ``file_path``
      → cross-file dependency (boundary-aware matching).
    - Barrier tools (run_tests, run_lint, etc.) depend on ALL prior steps.
    - Steps with no dependency on each other land in the same parallel group.

    Returns a list of groups.  Steps within a group are independent
    and can run concurrently.
    """
    if not steps:
        return []

    # Track which group each step belongs to
    group_idx: list[int] = []  # group index for each step

    # Map file_path → latest step index that touches it
    file_owners: dict[str, int] = {}

    # Minimum group for steps following the most recent barrier
    min_group_after_barrier = 0

    for i, step in enumerate(steps):
        max_dep_group = min_group_after_barrier - 1

        # Barrier tools: depend on everything before them
        if step.tool in _BARRIER_TOOLS or bool(_step_allowed_tool_names(step) & _BARRIER_TOOLS):
            if group_idx:
                max_dep_group = max(max_dep_group, max(group_idx))
            group_idx.append(max_dep_group + 1)
            # Steps after a barrier must come after the barrier group
            min_group_after_barrier = max_dep_group + 2
            file_owners.clear()
            continue

        step_paths = _step_may_change_paths(step)
        if not step_paths and step.file_path:
            step_paths = {_normalize_path(step.file_path)}

        # Same file_path → sequential dependency
        for step_path in step_paths:
            if step_path and step_path in file_owners:
                dep_step = file_owners[step_path]
                max_dep_group = max(max_dep_group, group_idx[dep_step])

        # Cross-file reference: check if the structured job contract mentions
        # any previously-touched file (boundary-aware).
        searchable = " ".join(
            part
            for part in (
                step.job or "",
                step.instruction or "",
                step.reason or "",
                step.output_shape or "",
                _step_success_check_text(step),
                step.file_path or "",
                " ".join(
                    f"{inp.source} {inp.details}".strip()
                    for inp in step.inputs
                    if inp.source or inp.details
                ),
                " ".join(
                    f"{target.path} {target.change}".strip()
                    for target in step.may_change
                    if target.path or target.change
                ),
            )
            if part
        )
        for fpath, dep_step in file_owners.items():
            if _path_mentioned_in(fpath, searchable):
                max_dep_group = max(max_dep_group, group_idx[dep_step])

        group_idx.append(max_dep_group + 1)

        # Register this step's mutation targets
        for step_path in step_paths:
            file_owners[step_path] = i

    # Collect groups
    num_groups = max(group_idx) + 1 if group_idx else 0
    groups: list[list[PlanStep]] = [[] for _ in range(num_groups)]
    for i, step in enumerate(steps):
        groups[group_idx[i]].append(step)

    return groups


async def execute_plan(
    plan: ExecutionPlan,
    task: str,
    repo_root: str,
    ws: WorkflowSession | None,
    llm_client: "LLMClient",
    context: str,
    branch_name: str,
    base_branch: str = "",
    conversation_logger: Callable | None = None,
    session_id: str = "",
    expert_llm_client: "LLMClient | None" = None,
    dispatcher: WSMessageDispatcher | None = None,
) -> str:
    """Execute each plan step sequentially with a constrained LLM."""
    _clear_incomplete_file(repo_root)
    if dispatcher:
        dispatcher.enter_execution_mode()
    # Shared telemetry context for all per-step calls. Mutated in place by
    # chat_with_tools so make_tool_executor can link tool_executions to
    # the most recent trace_uuid.
    exec_telemetry = {
        "repo_root": repo_root,
        "session_id": session_id,
        "phase": "implementation",
        "role": "primary",
    }
    tool_executor = make_tool_executor(
        repo_root,
        ws,
        session_id,
        llm_client=llm_client,
        dispatcher=dispatcher,
        telemetry_context=exec_telemetry,
    )
    total_steps = len(plan.steps) + len(getattr(plan, "tdd_test_steps", None) or [])
    all_executed: list[ToolCall] = []
    step_explanations: list[str] = []
    completed_descriptions: list[str] = []
    step_artifacts: dict[str, str] = {}  # {relative_path: file_content}
    implicit_modified_files: set[str] = set()
    _artifacts_lock = asyncio.Lock()

    # Send execution checklist to the extension for progress UI
    checklist_steps = []
    for step in plan.steps:
        checklist_steps.append(
            {
                "step_index": step.step_number - 1,
                "description": (step.job or step.instruction)[:120],
                "tool": step.tool,
                "file_path": step.file_path or "",
            }
        )
    if getattr(plan, "tdd_test_steps", None):
        tdd_steps = [
            {
                "step_index": s.step_number - 1,
                "description": f"[TEST] {s.instruction[:110]}",
                "tool": s.tool,
                "file_path": s.file_path or "",
            }
            for s in plan.tdd_test_steps
        ]
        checklist_steps = tdd_steps + checklist_steps
    await ws_send(
        ws,
        "execution_checklist",
        {
            "steps": checklist_steps,
            "total": len(checklist_steps),
        },
    )

    # Build the system prompt once (shared across all steps)
    naming_text = format_naming_conventions_for_prompt(
        getattr(plan, "naming_conventions", []) or [],
    )
    name_registry_text = format_name_registry_for_prompt(
        getattr(plan, "name_registry", []) or [],
    )
    system_prompt = build_step_system_prompt(
        load_execution_context(repo_root),
        naming_conventions=naming_text,
        name_registry=name_registry_text,
    )

    # Callbacks for WebSocket progress + conversation logging.
    cb = build_workflow_callbacks(
        ws,
        conversation_logger=conversation_logger,
    )

    # ── Helper: execute a single step with a given client/tools ─────
    async def _run_step(
        step,
        client,
        tools,
        executor,
        sys_prompt,
        label_prefix: str = "",
        telemetry: dict | None = None,
    ) -> bool:
        """Execute one plan step, collecting artifacts and progress."""
        step_label = f"{label_prefix}Step {step.step_number}"
        logger.info(
                "Executing %s/%d: %s %s — %s",
            step_label,
            total_steps,
            ",".join(sorted(_step_allowed_tool_names(step))),
            step.file_path,
            (step.job or step.instruction)[:80],
        )

        await ws_send(
            ws,
            "checkpoint",
            {
                "step_index": step.step_number - 1,
                "step_description": (f"{step_label}: {(step.job or step.instruction)[:100]}"),
                "status": "running",
                "head_commit_sha": None,
            },
        )

        user_msg = build_step_user_message(
            step,
            completed_descriptions,
            total_steps,
            step_artifacts=step_artifacts,
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
        attempted_calls: list[ToolCall] = []
        successful_calls: list[ToolCall] = []
        task_complete_seen = False
        pre_step_snapshot: dict[str, tuple[int, int]] | None = None
        implicit_changed_paths: list[str] = []

        def _build_step_refresh(
            current_messages: list[dict],
        ) -> list[dict]:
            """Rebuild messages from fresh disk state for context refresh."""
            fresh_ctx = load_execution_context(repo_root)
            fresh_sys = build_step_system_prompt(
                fresh_ctx,
                naming_conventions=naming_text,
                name_registry=name_registry_text,
            )
            fresh_user = build_step_user_message(
                step,
                completed_descriptions,
                total_steps,
                step_artifacts=step_artifacts,
            )
            pad = scratchpad.read_scratchpad(repo_root, session_id)
            jrnl = read_journal(repo_root, session_id)
            new_messages: list[dict] = [
                {"role": "system", "content": fresh_sys},
                {"role": "user", "content": fresh_user},
            ]
            refresh_parts = ["[CONTEXT REFRESHED]"]
            if jrnl:
                refresh_parts.append(f"SESSION JOURNAL (permanent findings):\n{jrnl}")
            if pad:
                refresh_parts.append(f"SCRATCHPAD (current state):\n{pad}")
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
                        "content": ("[CONTEXT REFRESHED]\n\nContinue working on the current step."),
                    }
                )
            ws_send_nowait(
                ws,
                "context_refreshed",
                {
                    "message": "Step context refreshed.",
                },
            )
            return new_messages

        def _build_step_reminder() -> str:
            parts = [
                f"REMINDER — STEP {step.step_number} OF {total_steps}",
                f"Job: {step.job or step.instruction}",
                f"Allowed tools: {', '.join(sorted(_step_allowed_tool_names(step)))}",
            ]
            paths = sorted(_step_may_change_paths(step))
            if paths:
                parts.append(f"May change: {', '.join(paths)}")
            if step.must_not_change:
                parts.append(f"Must not change: {', '.join(step.must_not_change)}")
            if step.output_shape:
                parts.append(f"Required output: {step.output_shape}")
            if step.success_checks:
                checks = "; ".join(check.description for check in step.success_checks)
                parts.append(f"Success checks: {checks}")
            return "\n".join(parts)

        def _build_step_text_only_nudge() -> str:
            target = f" on `{step.file_path}`" if step.file_path else ""
            return (
                f"This is still {step_label}. Use tools to do the work before replying with prose. "
                f"Complete the job contract{target}, satisfy its success checks, then call "
                "task_complete when the step is truly finished. If blocked, follow the blocked protocol."
            )

        async def _step_executor(name: str, arguments: dict) -> str:
            nonlocal pre_step_snapshot, implicit_changed_paths

            scope_error = _step_scope_error(step, name, arguments)
            if scope_error:
                return scope_error

            if name in _IMPLICIT_MUTATION_TOOLS and pre_step_snapshot is None:
                pre_step_snapshot = await asyncio.to_thread(_snapshot_repo_state, repo_root)

            result = await executor(name, arguments)

            call = ToolCall(
                tool_name=name,
                parameters=arguments,
                description=f"{name} {arguments.get('path', arguments.get('command', ''))}",
            )
            attempted_calls.append(call)

            if not _tool_result_failed(result):
                successful_calls.append(call)

            return result

        def _validate_step_completion() -> str | None:
            nonlocal task_complete_seen
            task_complete_seen = True
            return _step_completion_error(
                step,
                task_complete_seen=True,
                successful_calls=successful_calls,
                attempted_calls=attempted_calls,
            )

        _, explanation = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor_fn=_step_executor,
            max_turns=settings.implementation_max_turns,
            max_tokens=settings.implementation_max_tokens,
            task_reminder=_build_step_reminder,
            reminder_interval=1,
            text_only_nudge=_build_step_text_only_nudge(),
            on_tool_call=cb.on_tool_call,
            on_tool_result=cb.on_tool_result,
            on_content=cb.on_content,
            on_thinking=cb.on_thinking,
            on_metrics=cb.on_metrics,
            on_metrics_reset=cb.on_metrics_reset,
            on_context_refresh=_build_step_refresh,
            dispatcher=dispatcher,
            telemetry_context=telemetry or exec_telemetry,
            task_complete_validator=_validate_step_completion,
        )

        if pre_step_snapshot is not None:
            post_step_snapshot = await asyncio.to_thread(_snapshot_repo_state, repo_root)
            implicit_changed_paths = _diff_repo_state(
                pre_step_snapshot,
                post_step_snapshot,
            )

        completion_error = _step_completion_error(
            step,
            task_complete_seen=task_complete_seen,
            successful_calls=successful_calls,
            attempted_calls=attempted_calls,
        )
        allowed_paths = _step_may_change_paths(step)
        if not completion_error and allowed_paths and implicit_changed_paths:
            out_of_scope = [
                path for path in implicit_changed_paths if _normalize_path(path) not in allowed_paths
            ]
            if out_of_scope:
                completion_error = (
                    "Step changed files outside its `may_change` boundary: "
                    f"{', '.join(out_of_scope)}. Allowed: {', '.join(sorted(allowed_paths))}."
                )
        if completion_error:
            detail = completion_error
            if explanation.strip():
                detail += f"\n\nModel output:\n{explanation.strip()}"
            await asyncio.to_thread(
                _append_incomplete_entry,
                repo_root,
                step_label=step_label,
                detail=detail,
            )
            await ws_send(
                ws,
                "checkpoint",
                {
                    "step_index": step.step_number - 1,
                    "step_description": (f"{step_label}: {(step.job or step.instruction)[:100]}"),
                    "status": "failed",
                    "head_commit_sha": None,
                },
            )
            logger.warning("%s failed validation: %s", step_label, completion_error)
            return False

        # Update shared state under lock for parallel safety
        async with _artifacts_lock:
            all_executed.extend(successful_calls)
            if explanation.strip():
                step_explanations.append(f"{step_label}: {explanation.strip()}")
            completed_descriptions.append(f"{step_label}: {step.job or step.instruction}")

            # Collect files created/modified for cross-step context
            artifact_budget = int(settings._active_context_window * 0.10 * 3.5)
            changed_paths = {
                tc.parameters.get("path", "")
                for tc in successful_calls
                if tc.tool_name in ("create_file", "edit_file") and tc.parameters.get("path")
            }
            changed_paths.update(implicit_changed_paths)
            implicit_modified_files.update(implicit_changed_paths)
            for fpath in sorted(p for p in changed_paths if p):
                full = os.path.join(repo_root, fpath)
                try:
                    if os.path.isfile(full):
                        content = await asyncio.to_thread(
                            Path(full).read_text,
                            encoding="utf-8",
                            errors="replace",
                        )
                        step_artifacts[fpath] = content
                except Exception:
                    pass

            while sum(len(c) for c in step_artifacts.values()) > artifact_budget and step_artifacts:
                oldest_key = next(iter(step_artifacts))
                del step_artifacts[oldest_key]

        await ws_send(
            ws,
            "checkpoint",
            {
                "step_index": step.step_number - 1,
                "step_description": (f"{step_label}: {(step.job or step.instruction)[:100]}"),
                "status": "completed",
                "head_commit_sha": None,
            },
        )
        return True

    # ── TDD three-phase execution ─────────────────────────────────
    tdd_active = (
        False
        and plan.tdd_test_steps
        and expert_llm_client is not None
        and settings._active_context_window > 32768
    )

    halted_early = False

    if tdd_active:
        halted_early = not await _run_tdd_execution(
            plan=plan,
            repo_root=repo_root,
            ws=ws,
            llm_client=llm_client,
            expert_llm_client=expert_llm_client,
            session_id=session_id,
            dispatcher=dispatcher,
            cb=cb,
            step_artifacts=step_artifacts,
            run_step=_run_step,
        )
    else:
        # ── Normal (non-TDD) execution ────────────────────────────
        step_groups = _build_step_groups(plan.steps)

        for group in step_groups:
            if len(group) == 1 or settings.num_parallel <= 1:
                for step in group:
                    step_ok = await _run_step(
                        step,
                        llm_client,
                        build_implementation_tools(),
                        tool_executor,
                        system_prompt,
                    )
                    if not step_ok:
                        halted_early = True
                        break
                if halted_early:
                    break
            else:
                # Run independent steps concurrently
                logger.info(
                    "Parallel group: %d steps (%s)",
                    len(group),
                    ", ".join(s.file_path or s.tool for s in group),
                )
                group_results = await asyncio.gather(
                    *[
                        _run_step(
                            step,
                            llm_client,
                            build_implementation_tools(),
                            tool_executor,
                            system_prompt,
                        )
                        for step in group
                    ]
                )
                if not all(group_results):
                    halted_early = True
                    break

    # ── All steps done ───────────────────────────────────────────
    files_modified = sorted(
        {
            tc.parameters.get("path", "")
            for tc in all_executed
            if tc.tool_name in ("create_file", "edit_file") and tc.parameters.get("path")
        }
        | implicit_modified_files
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
                session_id,
                conversation_logger=conversation_logger,
                expert_llm_client=expert_llm_client,
                dispatcher=dispatcher,
                allowed_files=plan.affected_files,
                task=task,
            )

    # Check for incomplete.md
    incomplete_path = os.path.join(
        repo_root,
        ".lean_ai",
        "incomplete.md",
    )
    incomplete_content = ""
    if os.path.isfile(incomplete_path):
        try:
            with open(incomplete_path, encoding="utf-8") as f:
                incomplete_content = f.read()
        except Exception:
            pass

    completed_step_count = len(completed_descriptions)
    summary = (
        f"Completed {completed_step_count}/{total_steps} plan steps, "
        f"{len(all_executed)} tool calls. "
        f"Files modified: "
        f"{', '.join(files_modified) if files_modified else 'none'}."
    )
    if halted_early:
        summary += "\n\n⚠️ Execution halted early because a plan step did not complete cleanly."
    if step_explanations:
        summary += "\n\n" + "\n".join(step_explanations)
    if incomplete_content:
        summary += (
            f"\n\n⚠️ Some steps had issues — see .lean_ai/incomplete.md:\n{incomplete_content}"
        )
    if validation_results:
        failed = {k: r for k, r in validation_results.items() if not r["success"]}
        if failed:
            summary += "\n\n⚠️ Post-validation failures:"
            for name, result in failed.items():
                summary += f"\n  {name}: {result['output'][:200]}"
        else:
            summary += "\n\n✓ Post-validation passed."

    journal_content = read_journal(repo_root, session_id)
    if journal_content:
        summary += f"\n\nSession Journal:\n{journal_content}"

    # ── Invalidate metadata cache for modified files ──
    if files_modified:
        invalidate_metadata_cache_for_paths(repo_root, files_modified)

    # ── Incremental project_context.md update ──
    if files_modified and settings.enable_project_context:
        await _update_project_context(
            repo_root,
            ws,
            llm_client,
            files_modified,
        )

    # ── Auto-push to linked integrations (fire-and-forget) ──
    if settings.enable_integrations and settings.integration_auto_push:
        asyncio.create_task(
            auto_push_integration(repo_root, session_id),
        )

    # ── Cross-session memory extraction (fire-and-forget) ──
    if settings.enable_session_memory:
        asyncio.create_task(
            auto_extract_session_memories(
                repo_root,
                session_id,
                task,
                plan,
                llm_client,
                files_modified,
                validation_results,
            ),
        )

    # ── Training archive: execution_complete event ──
    try:
        from lean_ai.workflow.hooks import fire_workflow_event

        validation_passed = all(r.get("success", True) for r in (validation_results or {}).values())
        fire_workflow_event(
            repo_root=repo_root,
            session_id=session_id,
            event_type="execution_complete",
            payload={
                "task": task,
                "files_modified_count": len(files_modified),
                "validation_passed": validation_passed,
                "branch_name": branch_name,
                "base_branch": base_branch,
            },
        )
    except Exception:
        logger.debug(
            "execution_complete event capture failed (non-fatal)",
            exc_info=True,
        )

    # ── Retention pass (throttled to once per hour per workspace) ──
    try:
        from lean_ai.training.maintenance import run_retention_pass

        asyncio.create_task(run_retention_pass(repo_root))
    except Exception:
        logger.debug("retention pass scheduling failed (non-fatal)", exc_info=True)

    complete_data: dict = {
        "summary": summary,
        "files_modified": files_modified,
    }
    if branch_name:
        complete_data["plan_branch"] = branch_name
    if base_branch:
        complete_data["base_branch"] = base_branch
    await ws_send(ws, "complete", complete_data)
    logger.info(
        "Workflow complete: %d steps, %d tool calls, %d files",
        len(plan.steps),
        len(all_executed),
        len(files_modified),
    )

    # Build commit message
    task_summary = task[:72].replace("\n", " ")
    commit_msg = f"lean-ai: {task_summary}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg


async def _run_tdd_execution(
    *,
    plan: ExecutionPlan,
    repo_root: str,
    ws: WorkflowSession | None,
    llm_client: "LLMClient",
    expert_llm_client: "LLMClient",
    session_id: str,
    dispatcher: WSMessageDispatcher | None,
    cb,
    step_artifacts: dict[str, str],
    run_step: Callable,
) -> bool:
    """TDD two-phase execution: expert writes tests → primary implements."""

    # ── Phase A: Expert writes tests ──────────────────────────
    await ws_send(
        ws,
        "stage_status",
        {
            "stage": "tdd_test_writing",
            "status": "running",
            "summary": (f"TDD: Expert writing {len(plan.tdd_test_steps)} test step(s)..."),
        },
    )

    test_tool_executor = make_tool_executor(
        repo_root,
        ws,
        session_id,
        llm_client=expert_llm_client,
        dispatcher=dispatcher,
        telemetry_context={
            "repo_root": repo_root,
            "session_id": session_id,
            "phase": "tdd.write",
            "role": "expert",
        },
    )
    test_system_prompt = build_tdd_test_writing_prompt(
        load_execution_context(repo_root),
        implementation_plan_md=plan_to_markdown(plan),
        naming_conventions=format_naming_conventions_for_prompt(
            getattr(plan, "naming_conventions", []) or [],
        ),
        name_registry=format_name_registry_for_prompt(
            getattr(plan, "name_registry", []) or [],
        ),
    )

    for step in plan.tdd_test_steps:
        step_ok = await run_step(
            step,
            expert_llm_client,
            build_implementation_tools(),
            test_tool_executor,
            test_system_prompt,
            label_prefix="[TDD Test] ",
            telemetry={
                "repo_root": repo_root,
                "session_id": session_id,
                "phase": "tdd.write",
                "role": "expert",
            },
        )
        if not step_ok:
            await ws_send(
                ws,
                "stage_status",
                {
                    "stage": "tdd_test_writing",
                    "status": "done",
                    "summary": "TDD: Test writing halted because a step did not complete cleanly.",
                },
            )
            return False

    await ws_send(
        ws,
        "stage_status",
        {
            "stage": "tdd_test_writing",
            "status": "done",
            "summary": "TDD: All test steps complete.",
        },
    )

    # ── Phase C: Primary implements code ──────────────────────
    await ws_send(
        ws,
        "stage_status",
        {
            "stage": "tdd_implementation",
            "status": "running",
            "summary": (f"TDD: Primary implementing {len(plan.steps)} step(s)..."),
        },
    )

    tdd_impl_prompt = build_tdd_step_system_prompt(
        load_execution_context(repo_root),
        naming_conventions=format_naming_conventions_for_prompt(
            getattr(plan, "naming_conventions", []) or [],
        ),
        name_registry=format_name_registry_for_prompt(
            getattr(plan, "name_registry", []) or [],
        ),
    )
    incomplete_results: list[dict] = []
    for step in plan.steps:
        impl_executor = make_tool_executor(
            repo_root,
            ws,
            session_id,
            llm_client=llm_client,
            dispatcher=dispatcher,
            tdd_protect_tests=True,
            telemetry_context={
                "repo_root": repo_root,
                "session_id": session_id,
                "phase": "tdd.implement",
                "role": "primary",
            },
        )

        step_ok = await run_step(
            step,
            llm_client,
            build_tdd_implementation_tools(),
            impl_executor,
            tdd_impl_prompt,
            label_prefix="[TDD Impl] ",
            telemetry={
                "repo_root": repo_root,
                "session_id": session_id,
                "phase": "tdd.implement",
                "role": "primary",
            },
        )

        # Gather step-specific test commands for retry loop
        test_commands = _collect_step_test_commands(step)

        if not step_ok and not test_commands:
            # Step failed and has no test-based checks — record incomplete
            incomplete_results.append(
                {
                    "step_number": step.step_number,
                    "reason": f"Step did not complete cleanly: {_step_primary_label(step)}.",
                }
            )
            _append_incomplete_entry(
                repo_root,
                step_label=f"[TDD Impl] Step {step.step_number}",
                detail=f"Step did not complete cleanly: {_step_primary_label(step)}.",
            )
            continue

        if not step_ok and test_commands:
            # Step failed but has test commands — retry with test feedback
            attempts = 0
            last_test_output = ""
            while attempts < _TDD_MAX_RETRIES:
                attempts += 1
                logger.info(
                    "TDD retry %d/%d for step %d after test failure",
                    attempts,
                    _TDD_MAX_RETRIES,
                    step.step_number,
                )
                step_ok = await run_step(
                    step,
                    llm_client,
                    build_tdd_implementation_tools(),
                    impl_executor,
                    tdd_impl_prompt,
                    label_prefix=f"[TDD Impl Retry {attempts}] ",
                    telemetry={
                        "repo_root": repo_root,
                        "session_id": session_id,
                        "phase": "tdd.implement",
                        "role": "primary",
                    },
                )
                if step_ok:
                    break
                # Run tests to get failure output for next retry
                _, last_test_output = await _run_step_tests(repo_root, test_commands)

            if not step_ok:
                incomplete_results.append(
                    {
                        "step_number": step.step_number,
                        "reason": (
                            f"Step failed after {_TDD_MAX_RETRIES} retries. "
                            f"Last test output: {last_test_output[:500]}"
                        ),
                    }
                )
                _append_incomplete_entry(
                    repo_root,
                    step_label=f"[TDD Impl] Step {step.step_number}",
                    detail=(
                        f"Step failed after {_TDD_MAX_RETRIES} retries. "
                        f"Last test output: {last_test_output[:500]}"
                    ),
                )
                continue

        # Step succeeded — run tests and retry if they fail
        if test_commands:
            tests_pass, test_output = await _run_step_tests(repo_root, test_commands)
            if not tests_pass:
                attempts = 0
                while attempts < _TDD_MAX_RETRIES:
                    attempts += 1
                    logger.info(
                        "TDD retry %d/%d for step %d after test failure",
                        attempts,
                        _TDD_MAX_RETRIES,
                        step.step_number,
                    )
                    step_ok = await run_step(
                        step,
                        llm_client,
                        build_tdd_implementation_tools(),
                        impl_executor,
                        tdd_impl_prompt,
                        label_prefix=f"[TDD Impl Retry {attempts}] ",
                        telemetry={
                            "repo_root": repo_root,
                            "session_id": session_id,
                            "phase": "tdd.implement",
                            "role": "primary",
                        },
                    )
                    if not step_ok:
                        break
                    tests_pass, test_output = await _run_step_tests(repo_root, test_commands)
                    if tests_pass:
                        break

                if not tests_pass:
                    incomplete_results.append(
                        {
                            "step_number": step.step_number,
                            "reason": (
                                f"Tests still failing after {_TDD_MAX_RETRIES} retries. "
                                f"Last test output: {test_output[:500]}"
                            ),
                        }
                    )
                    _append_incomplete_entry(
                        repo_root,
                        step_label=f"[TDD Impl] Step {step.step_number}",
                        detail=(
                            f"Tests still failing after {_TDD_MAX_RETRIES} retries. "
                            f"Last test output: {test_output[:500]}"
                        ),
                    )
                    continue

    await ws_send(
        ws,
        "stage_status",
        {
            "stage": "tdd_implementation",
            "status": "done",
            "summary": "TDD: Implementation complete.",
        },
    )
    return True


async def _update_project_context(
    repo_root: str,
    ws: WorkflowSession | None,
    llm_client: "LLMClient",
    files_modified: list[str],
) -> None:
    """Incremental project_context.md update after execution."""
    await ws_send(
        ws,
        "stage_status",
        {
            "stage": "context_update",
            "status": "running",
            "summary": (f"Updating project context with {len(files_modified)} modified file(s)..."),
        },
    )
    try:
        from lean_ai.context.generation import update_project_context

        ctx_path = await update_project_context(
            repo_root,
            files_modified,
            llm_client,
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
            logger.info(
                "project_context.md update skipped (no changes needed)",
            )
            await ws_send(
                ws,
                "stage_status",
                {
                    "stage": "context_update",
                    "status": "done",
                    "summary": ("Project context update skipped (no changes needed)."),
                },
            )
    except Exception as exc:
        logger.warning(
            "Incremental context update failed (non-fatal): %s",
            exc,
        )
        await ws_send(
            ws,
            "stage_status",
            {
                "stage": "context_update",
                "status": "done",
                "summary": f"Project context update failed: {exc}",
            },
        )
