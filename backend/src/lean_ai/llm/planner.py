"""5-phase decomposed planning pipeline with structured output.

Phase 1: Scope analysis
Phase 2: File identification + content reading (with codebase exploration via tools)
Phase 3: Change design (specific changes per file, using read file content)
Phase 4: Risk assessment
Phase 5: Structured plan assembly (produces ExecutionPlan via chat_structured)

Each phase is a focused LLM call. The planner uses read-only tools
(read_file, list_directory, directory_tree) during Phase 2 to explore
the codebase and read every file it plans to modify.  The file content
flows into Phase 3/5 so the plan contains enough context for the
executor to construct accurate tool calls without re-exploring.
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
                    "- What is out of scope\n"
                    "- Key assumptions\n"
                    "- Patterns to follow from the existing codebase"
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
                "Identify EVERY file that needs to be created or modified. "
                "IMPORTANT: Use read_file to read the FULL CONTENT of every file "
                "you plan to modify. The content you read will be included in the "
                "plan so the executor can make accurate edits without re-reading.\n\n"
                "Also read files that contain patterns the executor should follow "
                "when creating new files.\n\n"
                "Use the read_file, list_directory, and directory_tree tools to "
                "explore the codebase thoroughly. Then provide:\n\n"
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
        from lean_ai.tools.file_ops import read_file

        if name == "read_file":
            result = await read_file(
                path=arguments.get("path", ""),
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
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
        max_turns=25,
        max_tokens=phase_max_tokens,
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
                    f"FILES IDENTIFIED AND READ:\n{file_identification}\n\n"
                    "For each identified file, describe the SPECIFIC changes:\n"
                    "- Functions/classes to add or modify (with signatures)\n"
                    "- What section of the file to modify (reference the content "
                    "you read in Phase 2)\n"
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
                    "- Rollback strategy?"
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
                    f"TASK: {task}\n\n"
                    f"SCOPE:\n{scope}\n\n"
                    f"FILES AND CONTENT:\n{file_identification}\n\n"
                    f"CHANGE DESIGN:\n{change_design}\n\n"
                    f"RISKS:\n{risks}\n\n"
                    "Assemble the final execution plan as structured JSON. "
                    "Each step must represent ONE tool call.\n\n"
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
                    "}"
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
