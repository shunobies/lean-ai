"""6-phase decomposed planning pipeline with structured output.

Phase 1: Scope analysis
Phase 2: File identification + content reading (with codebase exploration via tools)
Phase 2.5: Compress exploration results (reduces token bloat for downstream phases)
Phase 3: Change design (specific changes per file, using compressed file summary)
Phase 4: Risk assessment
Phase 5: Structured plan assembly (produces ExecutionPlan via chat_structured)

Each phase is a focused LLM call. The planner uses read-only tools
(read_file, list_directory, directory_tree, grep_files) during Phase 2
to explore the codebase and read every file it plans to modify.
Phase 2.5 compresses the raw exploration output into a structured
summary to increase information density and reduce "lost in the middle"
attention degradation in downstream phases.
"""

import json
import logging
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan
from lean_ai.llm.prompts import CLARIFICATION_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT
from lean_ai.llm.tool_definitions import PLANNING_TOOLS

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


async def _send_stage(ws: WebSocket | None, summary: str) -> None:
    """Send a planning stage_status message if WebSocket is available."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send
    await ws_send(ws, "stage_status", {
        "stage": "planning",
        "status": "running",
        "summary": summary,
    })


async def assess_clarity(
    task: str,
    llm_client: "LLMClient",
    context: str = "",
) -> list[str] | None:
    """Assess whether a task is clear enough to plan.

    Returns None if the task is clear, or a list of clarifying questions.
    """
    logger.info("Assessing task clarity")

    response = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": CLARIFICATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK:\n{task}\n\n"
                    f"PROJECT CONTEXT:\n{context[:5000]}\n\n"
                    "Is this task clear enough to create a detailed "
                    "implementation plan?"
                ),
            },
        ],
        max_tokens=1024,
    )

    stripped = response.strip()
    if stripped.upper().startswith("CLEAR"):
        return None

    # Try to parse as JSON array of questions
    try:
        questions = json.loads(stripped)
        if isinstance(questions, list) and all(isinstance(q, str) for q in questions):
            return questions[:5]
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: extract lines that look like questions
    lines = [
        ln.strip().lstrip("- ").lstrip("0123456789.)")
        for ln in stripped.splitlines()
        if ln.strip() and "?" in ln
    ]
    return lines[:5] if lines else [stripped]


async def create_plan(
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    context: str = "",
    revision_context: str | None = None,
    ws: WebSocket | None = None,
) -> ExecutionPlan:
    """Create a plan using 5-phase decomposed planning.

    Args:
        task: The user's task description (may include clarification answers).
        repo_root: Path to the repository root.
        llm_client: LLM client for making calls.
        context: Pre-assembled context (project context, search results, etc.).
        revision_context: If revising, the previous plan JSON + user feedback.
        ws: Optional WebSocket for streaming stage progress.

    Returns:
        Structured ExecutionPlan ready for per-step execution.
    """
    if revision_context:
        return await _revise_plan(task, revision_context, llm_client, context, ws)

    phase_max_tokens = settings.ollama_max_tokens

    # Phase 1: Scope Analysis
    await _send_stage(ws, "Phase 1: Analyzing scope...")
    logger.info("Planning Phase 1: Scope analysis")
    scope = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CODEBASE CONTEXT:\n{context}\n\n"
                    "Analyze the scope of this task. In 300-500 words, describe:\n"
                    "- What needs to change\n"
                    "- **Data flow and consumers**: For every model, table, "
                    "schema, or data structure being modified, trace ALL "
                    "downstream consumers — controllers that query it, views "
                    "that display it, API resources/transformers that serialize "
                    "it, forms that accept input for it, tests that assert on "
                    "it. These consumers likely need updates too.\n"
                    "- What is out of scope\n"
                    "- Key assumptions\n"
                    "- Patterns to follow from the existing codebase\n\n"
                    "IMPORTANT: If the task mentions specific files to modify, "
                    "treat that list as a STARTING POINT, not an exhaustive "
                    "list. The codebase may have additional files that depend "
                    "on the changed data and need corresponding updates."
                ),
            },
        ],
        max_tokens=phase_max_tokens,
    )

    # Phase 2: File Identification + Content Reading (with tool access)
    await _send_stage(ws, "Phase 2: Exploring codebase and reading files...")
    logger.info("Planning Phase 2: File identification and reading")
    phase2_messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"TASK: {task}\n\n"
                f"SCOPE ANALYSIS:\n{scope}\n\n"
                f"CODEBASE CONTEXT:\n{context}\n\n"
                "Identify EVERY file that needs to be created or modified.\n\n"
                "CRITICAL — TRACE ALL CONSUMERS:\n"
                "Before finalizing the file list, use grep_files to search for "
                "references to every model, class, table, route, or component "
                "being modified. For example, if you are modifying a Customer "
                "model, search for 'Customer' across the codebase to find "
                "controllers, views, API resources, form requests, and tests "
                "that reference it. Files that read or display the data you are "
                "changing almost certainly need updates too.\n\n"
                "Do NOT treat file lists in the task description as exhaustive. "
                "The task may only mention the data layer (models, migrations) "
                "but omit presentation layer files (controllers, views, API "
                "resources) that also need changes.\n\n"
                "EXPLORATION STEPS:\n"
                "1. Use grep_files to find all references to modified entities\n"
                "2. Use directory_tree / list_directory to understand project structure\n"
                "3. Use read_file to read the FULL CONTENT of every file you "
                "plan to modify — the content will be included in the plan so "
                "the executor can make accurate edits without re-reading\n"
                "4. Also read files that contain patterns the executor should "
                "follow when creating new files\n\n"
                "OUTPUT FORMAT:\n\n"
                "FILES TO MODIFY (include key content you read):\n"
                "1. path/to/file — reason — relevant sections read\n\n"
                "FILES TO CREATE:\n"
                "1. path/to/new/file — purpose — patterns to follow\n\n"
                "FILES READ FOR CONTEXT (not modified, but content informs changes):\n"
                "1. path/to/source — what it contains"
            ),
        },
    ]

    # Let the LLM explore with read-only tools — generous budget
    async def _read_only_executor(name: str, arguments: dict) -> str:
        """Execute read-only tools for planning phase."""
        from lean_ai.tools.file_ops import grep_files, read_file

        if name == "read_file":
            result = await read_file(
                path=arguments.get("path", ""),
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
            )
            return result.output if result.success else result.error or "Error"
        elif name == "grep_files":
            result = await grep_files(
                pattern=arguments.get("pattern", ""),
                repo_root=repo_root,
                file_glob=arguments.get("file_glob"),
            )
            return result.output if result.success else result.error or "Error"
        elif name == "list_directory":
            from pathlib import Path
            target = Path(repo_root) / arguments.get("path", "")
            if not target.is_dir():
                return f"Not a directory: {arguments.get('path', '')}"
            max_entries = arguments.get("max_entries", 100)
            entries = sorted(target.iterdir())[:max_entries]
            lines = []
            for e in entries:
                prefix = "d" if e.is_dir() else "f"
                lines.append(f"  {prefix}  {e.name}")
            return "\n".join(lines) or "(empty)"
        elif name == "directory_tree":
            from lean_ai.indexer.tree import list_repo_tree
            sub_path = arguments.get("path", "")
            tree_root = f"{repo_root}/{sub_path}" if sub_path else repo_root
            entries = list_repo_tree(tree_root)
            max_depth = arguments.get("max_depth", 3)
            lines = []
            for e in entries[:200]:
                depth = e.path.count("/")
                if depth <= max_depth:
                    indent = "  " * depth
                    lines.append(f"{indent}{e.path.split('/')[-1]}")
            return "\n".join(lines) or "(empty)"
        return f"Unknown tool: {name}"

    tool_calls, file_identification = await llm_client.chat_with_tools(
        messages=phase2_messages,
        tools=PLANNING_TOOLS,
        tool_executor_fn=_read_only_executor,
        max_turns=50,
        max_tokens=phase_max_tokens,
        task_reminder=(
            f"REMINDER — You are exploring the codebase for this task: {task}\n\n"
            "Have you used grep_files to trace ALL consumers of modified "
            "entities? Do NOT finalize until you have searched for every "
            "model/class being changed and read every file that references it."
        ),
        reminder_interval=15,
    )

    # Phase 2.5: Compress exploration results
    # Raw file_identification from Phase 2 can be very large (50 turns of
    # grep/read output).  Compressing it increases information density and
    # keeps downstream phases within the high-attention zones of the context
    # window ("Lost in the Middle" mitigation).
    await _send_stage(ws, "Compressing exploration results...")
    logger.info(
        "Planning Phase 2.5: Compressing file identification "
        "(%d chars raw)", len(file_identification),
    )
    file_summary = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"EXPLORATION RESULTS:\n{file_identification}\n\n"
                    "Compress the exploration results into a structured "
                    "summary.\n\n"
                    "For each file that needs to be CREATED or MODIFIED:\n"
                    "- File path\n"
                    "- Why it needs changes (one line)\n"
                    "- The specific code sections that will be modified "
                    "(only the relevant lines, not the entire file)\n\n"
                    "For files read for CONTEXT only:\n"
                    "- File path and the pattern/structure to follow "
                    "(compact)\n\n"
                    "IMPORTANT: Preserve all file paths, line numbers, and "
                    "code snippets needed to construct accurate edits. Drop "
                    "narrative, tool call logs, and redundant file content."
                ),
            },
        ],
        max_tokens=phase_max_tokens,
    )
    logger.info(
        "Phase 2.5: compressed %d chars -> %d chars (%.0f%% reduction)",
        len(file_identification), len(file_summary),
        (1 - len(file_summary) / max(len(file_identification), 1)) * 100,
    )

    # Phase 3: Change Design
    await _send_stage(ws, "Phase 3: Designing specific changes...")
    logger.info("Planning Phase 3: Change design")
    change_design = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"SCOPE:\n{scope}\n\n"
                    f"FILES IDENTIFIED AND READ:\n{file_summary}\n\n"
                    "For each identified file, describe the SPECIFIC changes:\n"
                    "- Functions/classes to add or modify (with signatures)\n"
                    "- What section of the file to modify (reference the content "
                    "from the file summary)\n"
                    "- Integration points with existing code\n"
                    "- For new files: structure, imports, patterns to follow "
                    "from existing files you read\n"
                    "- For test/lint commands: the exact command string"
                ),
            },
        ],
        max_tokens=phase_max_tokens,
    )

    # Phase 4: Risk Assessment
    await _send_stage(ws, "Phase 4: Assessing risks...")
    logger.info("Planning Phase 4: Risk assessment")
    risks = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CHANGE DESIGN:\n{change_design}\n\n"
                    "Evaluate risks, failure modes, and edge cases:\n"
                    "- What could break?\n"
                    "- Security implications?\n"
                    "- Backward compatibility concerns?\n"
                    "- Rollback strategy?\n"
                    "- **Missing file coverage**: Are there files that consume "
                    "or display the modified data that are NOT included in the "
                    "change design? For example: controllers that query the "
                    "modified model, views/templates that render the data, API "
                    "resources that serialize it, form requests that validate "
                    "it, tests that assert on it. List any files that SHOULD "
                    "be modified but are currently missing from the plan."
                ),
            },
        ],
        max_tokens=phase_max_tokens,
    )

    # Phase 5: Structured Plan Assembly
    await _send_stage(ws, "Phase 5: Assembling structured plan...")
    logger.info("Planning Phase 5: Structured plan assembly")
    plan = await llm_client.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    # -- U-curve optimized ordering --
                    # Start (high attention): task + risks (actionable)
                    # Middle (lower attention): reference material
                    # End (high attention): assembly rules + checklist
                    f"TASK: {task}\n\n"
                    f"RISKS AND GAPS:\n{risks}\n\n"
                    f"CHANGE DESIGN:\n{change_design}\n\n"
                    f"FILE SUMMARY:\n{file_summary}\n\n"
                    f"SCOPE:\n{scope}\n\n"
                    "Assemble the final execution plan as structured JSON. "
                    "Each step must represent ONE tool call.\n\n"
                    "IMPORTANT: If the risk assessment identified missing files "
                    "(files that consume or display the modified data but were "
                    "not in the original change design), you MUST include steps "
                    "to update those files too. The plan must cover the full "
                    "data flow — not just the data layer.\n\n"
                    "RULES FOR STEPS:\n"
                    "- Use 'create_file' for new files, 'edit_file' for "
                    "modifications to existing files\n"
                    "- Use 'run_tests' or 'run_lint' for verification steps\n"
                    "- For edit_file steps: in the instruction field, describe "
                    "EXACTLY what section to find and what to replace it with. "
                    "Reference line numbers and content from the files you read. "
                    "In the context field, include the relevant section of the "
                    "file that will be modified (the actual text the executor "
                    "will need to construct search blocks).\n"
                    "- For create_file steps: in the instruction field, describe "
                    "what the file should contain — imports, classes, functions, "
                    "their purpose, and patterns to follow. In the context field, "
                    "include content from related files that show the pattern.\n"
                    "- For run_tests/run_lint steps: put the exact command in "
                    "the instruction field. Leave file_path and context empty.\n"
                    "- Order steps so dependencies come first\n"
                    "- Include verification steps (run_tests/run_lint) after "
                    "groups of related changes\n\n"
                    "EXAMPLE STEP (edit_file):\n"
                    '{\n'
                    '  "step_number": 3,\n'
                    '  "tool": "edit_file",\n'
                    '  "file_path": "src/config.py",\n'
                    '  "instruction": "Find the Settings class (around line 15). '
                    "After the 'port: int = 8080' field, add a new field: "
                    "'debug: bool = False'. Keep the existing fields unchanged."
                    '",\n'
                    '  "context": "class Settings:\\n    port: int = 8080\\n'
                    '    host: str = \\"localhost\\""\n'
                    "}\n\n"
                    "EXAMPLE STEP (create_file):\n"
                    '{\n'
                    '  "step_number": 5,\n'
                    '  "tool": "create_file",\n'
                    '  "file_path": "tests/test_config.py",\n'
                    '  "instruction": "Create a test file for the Settings '
                    "class. Import from lean_ai.config. Test that default "
                    "debug is False and that it can be overridden. Follow the "
                    'test pattern from tests/test_other.py.",\n'
                    '  "context": "# Pattern from tests/test_other.py:\\n'
                    "import pytest\\nfrom lean_ai.config import Settings\\n"
                    '..."\n'
                    "}\n\n"
                    "EXAMPLE STEP (run_tests):\n"
                    '{\n'
                    '  "step_number": 6,\n'
                    '  "tool": "run_tests",\n'
                    '  "file_path": "",\n'
                    '  "instruction": "pytest tests/test_config.py -v",\n'
                    '  "context": ""\n'
                    "}\n\n"
                    "FINAL CHECKLIST — verify before producing the plan:\n"
                    "- Every file identified in the risk assessment as missing "
                    "is included as a step\n"
                    "- The plan covers the full data flow: "
                    "model -> controller -> view\n"
                    "- Each edit_file step has specific line references and "
                    "context\n"
                    "- Steps are ordered so dependencies come first\n"
                    "- Verification steps (run_tests/run_lint) follow groups "
                    "of changes"
                ),
            },
        ],
        schema=ExecutionPlan,
        max_tokens=settings.ollama_max_tokens,
    )

    logger.info(
        "Plan created: %d steps, %d affected files",
        len(plan.steps), len(plan.affected_files),
    )
    return plan


async def _revise_plan(
    task: str,
    revision_context: str,
    llm_client: "LLMClient",
    context: str = "",
    ws: WebSocket | None = None,
) -> ExecutionPlan:
    """Revise an existing plan based on user feedback.

    Args:
        task: The original task.
        revision_context: Previous plan JSON + user feedback.
        llm_client: LLM client.
        context: Project context.
        ws: Optional WebSocket for progress.

    Returns:
        Revised ExecutionPlan.
    """
    await _send_stage(ws, "Revising plan based on feedback...")
    logger.info("Plan revision")
    plan = await llm_client.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CODEBASE CONTEXT:\n{context}\n\n"
                    f"REVISION CONTEXT:\n{revision_context}\n\n"
                    "Revise the plan based on the user's feedback. "
                    "Make targeted edits — don't rewrite from scratch. "
                    "Keep the same structured format with step_number, tool, "
                    "file_path, instruction, and context fields."
                ),
            },
        ],
        schema=ExecutionPlan,
        max_tokens=settings.ollama_max_tokens,
    )
    logger.info(
        "Plan revised: %d steps, %d affected files",
        len(plan.steps), len(plan.affected_files),
    )
    return plan
