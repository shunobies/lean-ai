"""Formatting helpers shared by external integration providers."""

from __future__ import annotations

from lean_ai.integrations.base import ModelUsage, SessionSummary

_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "ollama": "Ollama",
    "openai": "OpenAI",
    "serve": "Serve",
}


def _provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider.lower(), provider)


def _model_hosting_label(model: ModelUsage) -> str:
    return "local" if model.is_local else "cloud"


def _commit_shas(commits: list[dict], limit: int = 5) -> list[str]:
    shas: list[str] = []
    for commit in commits[:limit]:
        sha = (commit.get("sha") or commit.get("commit_sha") or "").strip()
        if sha:
            shas.append(sha[:8])
    return shas


def build_plain_session_summary_lines(summary: SessionSummary) -> list[str]:
    """Render a SessionSummary into plain-text lines."""
    lines = [
        f"Lean AI Session: {summary.session_id}",
        f"Task: {summary.task_description}",
        f"Status: {summary.status}",
    ]
    if summary.workflow_mode:
        lines.append(f"Mode: {summary.workflow_mode}")
    if summary.branch_name:
        lines.append(f"Branch: {summary.branch_name}")
    if summary.files_changed:
        lines.append(f"Files changed: {', '.join(summary.files_changed[:10])}")
    shas = _commit_shas(summary.commits)
    if shas:
        lines.append(f"Commits: {', '.join(shas)}")
    if summary.duration_seconds > 0:
        lines.append(f"Duration: {summary.duration_seconds / 60:.1f} minutes")
    if summary.tool_calls_count:
        lines.append(f"Tool calls: {summary.tool_calls_count}")
    if summary.models_used:
        lines.append("Models used:")
        for model in summary.models_used:
            lines.append(
                "  "
                f"{model.role}: {_provider_label(model.provider)} / {model.model}"
                f" ({_model_hosting_label(model)})"
            )
    return lines


def build_markdown_session_summary(summary: SessionSummary) -> str:
    """Render a SessionSummary into a GitHub-friendly markdown comment."""
    lines = [
        "### Lean AI Session",
        f"- Session: `{summary.session_id}`",
        f"- Task: {summary.task_description}",
        f"- Status: `{summary.status}`",
    ]
    if summary.workflow_mode:
        lines.append(f"- Mode: `{summary.workflow_mode}`")
    if summary.branch_name:
        lines.append(f"- Branch: `{summary.branch_name}`")
    if summary.files_changed:
        files = ", ".join(f"`{path}`" for path in summary.files_changed[:10])
        lines.append(f"- Files changed: {files}")
    shas = _commit_shas(summary.commits)
    if shas:
        lines.append(f"- Commits: {', '.join(f'`{sha}`' for sha in shas)}")
    if summary.duration_seconds > 0:
        lines.append(f"- Duration: {summary.duration_seconds / 60:.1f} minutes")
    if summary.tool_calls_count:
        lines.append(f"- Tool calls: {summary.tool_calls_count}")
    if summary.models_used:
        lines.extend(("", "### Models Used"))
        for model in summary.models_used:
            lines.append(
                "- "
                f"`{model.role}`: `{_provider_label(model.provider)} / {model.model}`"
                f" ({_model_hosting_label(model)})"
            )
    return "\n".join(lines)
