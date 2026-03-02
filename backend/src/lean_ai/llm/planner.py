"""5-phase decomposed planning pipeline.

Phase 1: Scope analysis
Phase 2: File identification (with codebase exploration via tools)
Phase 3: Change design (specific changes per file)
Phase 4: Risk assessment
Phase 5: Plan assembly

Each phase is a focused LLM call. The planner uses read-only tools
(read_file, list_directory, directory_tree) during Phase 2 to explore
the codebase before committing to a plan.
"""

import logging
from typing import TYPE_CHECKING

from lean_ai.config import settings
from lean_ai.llm.prompts import PLAN_SYSTEM_PROMPT
from lean_ai.llm.tool_definitions import PLANNING_TOOLS

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)

_PHASE_MAX_TOKENS = 2048


async def create_plan(
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    context: str = "",
    revision_context: str | None = None,
) -> str:
    """Create a plan using 5-phase decomposed planning.

    Args:
        task: The user's task description.
        repo_root: Path to the repository root.
        llm_client: LLM client for making calls.
        context: Pre-assembled context (project context, search results, etc.).
        revision_context: If revising, the previous plan + user feedback.

    Returns:
        The complete plan as a markdown string.
    """
    if revision_context:
        return await _revise_plan(task, revision_context, llm_client, context)

    # Phase 1: Scope Analysis
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
        max_tokens=_PHASE_MAX_TOKENS,
    )

    # Phase 2: File Identification (with tool access)
    logger.info("Planning Phase 2: File identification")
    phase2_messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"TASK: {task}\n\n"
                f"SCOPE ANALYSIS:\n{scope}\n\n"
                f"CODEBASE CONTEXT:\n{context}\n\n"
                "Identify EVERY file that needs to be created or modified. "
                "Use the read_file, list_directory, and directory_tree tools to "
                "explore the codebase. Then provide:\n\n"
                "FILES TO MODIFY:\n"
                "1. path/to/file — reason\n\n"
                "FILES TO CREATE:\n"
                "1. path/to/new/file — purpose\n\n"
                "FILES TO READ FOR CONTEXT (not modified):\n"
                "1. path/to/source — what it contains"
            ),
        },
    ]

    # Let the LLM explore with read-only tools
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
        max_turns=10,
        max_tokens=_PHASE_MAX_TOKENS,
    )

    # Phase 3: Change Design
    logger.info("Planning Phase 3: Change design")
    change_design = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"SCOPE:\n{scope}\n\n"
                    f"FILES IDENTIFIED:\n{file_identification}\n\n"
                    "For each identified file, describe the SPECIFIC changes:\n"
                    "- Functions/classes to add or modify\n"
                    "- Signatures and parameters\n"
                    "- Integration points with existing code\n"
                    "- For migrations: 'Read source.ext to extract X, write to target.ext'"
                ),
            },
        ],
        max_tokens=_PHASE_MAX_TOKENS,
    )

    # Phase 4: Risk Assessment
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
        max_tokens=_PHASE_MAX_TOKENS,
    )

    # Phase 5: Plan Assembly
    logger.info("Planning Phase 5: Plan assembly")
    plan = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"SCOPE:\n{scope}\n\n"
                    f"FILES:\n{file_identification}\n\n"
                    f"CHANGES:\n{change_design}\n\n"
                    f"RISKS:\n{risks}\n\n"
                    "Assemble the final plan. Use this structure:\n\n"
                    "## Scope\n"
                    "[scope summary]\n\n"
                    "## Steps\n"
                    "1. [step with file paths and specific changes]\n"
                    "2. ...\n\n"
                    "## Affected Files\n"
                    "- path/to/file\n\n"
                    "## Risks\n"
                    "- [risk]\n\n"
                    "## Test Strategy\n"
                    "[how to verify]\n\n"
                    "## Rollback\n"
                    "[rollback strategy]"
                ),
            },
        ],
        max_tokens=settings.ollama_max_tokens,
    )

    logger.info("Plan created (%d chars)", len(plan))
    return plan


async def _revise_plan(
    task: str,
    revision_context: str,
    llm_client: "LLMClient",
    context: str = "",
) -> str:
    """Revise an existing plan based on user feedback."""
    logger.info("Plan revision")
    plan = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CODEBASE CONTEXT:\n{context}\n\n"
                    f"REVISION CONTEXT:\n{revision_context}\n\n"
                    "Revise the plan based on the feedback. "
                    "Make targeted edits — don't rewrite from scratch."
                ),
            },
        ],
        max_tokens=settings.ollama_max_tokens,
    )
    return plan
