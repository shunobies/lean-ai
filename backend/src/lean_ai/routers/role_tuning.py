"""REST endpoints for explicit role-tuning prewarm operations."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from lean_ai.llm.prompt_registry import PromptScope
from lean_ai.llm.role_tuning import (
    ensure_expert_role_tuning,
    ensure_primary_role_tuning,
    ensure_request_role_tuning,
    load_role_tuning_profile,
    profile_is_current,
    prompt_scope_for_role,
    role_tuning_profile_path,
)
from lean_ai.routers.dependencies import expert_llm_client, llm_client, request_llm_client

role_tuning_router = APIRouter()


class PrewarmRoleTuningRequest(BaseModel):
    repo_root: str


class RoleTuningResult(BaseModel):
    role: str
    model_id: str
    status: str
    profile_path: str
    prompts_path: str
    warning: str | None = None


class PrewarmRoleTuningResponse(BaseModel):
    results: list[RoleTuningResult]


async def _prewarm_one_role(
    *,
    repo_root: str,
    role: str,
    scope: PromptScope,
    ensure_fn,
) -> RoleTuningResult:
    prompts_path = str(Path(repo_root) / ".lean_ai" / "prompts.yaml")
    profile_path = str(role_tuning_profile_path(repo_root, scope))
    existing = load_role_tuning_profile(repo_root, scope)
    if existing is not None and profile_is_current(existing, scope, repo_root=repo_root):
        return RoleTuningResult(
            role=role,
            model_id=scope.model_id,
            status="skipped",
            profile_path=profile_path,
            prompts_path=prompts_path,
            warning=existing.judge_warning,
        )

    await ensure_fn()
    profile = load_role_tuning_profile(repo_root, scope)
    return RoleTuningResult(
        role=role,
        model_id=scope.model_id,
        status="tuned",
        profile_path=profile_path,
        prompts_path=prompts_path,
        warning=profile.judge_warning if profile is not None else None,
    )


@role_tuning_router.post("/role-tuning/prewarm", response_model=PrewarmRoleTuningResponse)
async def prewarm_role_tuning(
    request: PrewarmRoleTuningRequest,
) -> PrewarmRoleTuningResponse:
    """Prewarm sharable role-tuning artifacts for the active workspace."""
    repo_root = request.repo_root
    results: list[RoleTuningResult] = []

    primary_scope = prompt_scope_for_role(llm_client, "primary")
    results.append(
        await _prewarm_one_role(
            repo_root=repo_root,
            role="primary",
            scope=primary_scope,
            ensure_fn=lambda: ensure_primary_role_tuning(
                repo_root=repo_root,
                assigned_client=llm_client,
                primary_client=llm_client,
                expert_client=expert_llm_client,
            ),
        )
    )

    request_client = request_llm_client or llm_client
    request_scope = prompt_scope_for_role(request_client, "request")
    results.append(
        await _prewarm_one_role(
            repo_root=repo_root,
            role="request",
            scope=request_scope,
            ensure_fn=lambda: ensure_request_role_tuning(
                repo_root=repo_root,
                assigned_client=request_client,
                primary_client=llm_client,
                expert_client=expert_llm_client,
            ),
        )
    )

    expert_client = expert_llm_client or llm_client
    expert_scope = prompt_scope_for_role(expert_client, "expert")
    results.append(
        await _prewarm_one_role(
            repo_root=repo_root,
            role="expert",
            scope=expert_scope,
            ensure_fn=lambda: ensure_expert_role_tuning(
                repo_root=repo_root,
                assigned_client=expert_client,
                primary_client=llm_client,
                expert_client=expert_llm_client,
            ),
        )
    )

    return PrewarmRoleTuningResponse(results=results)
