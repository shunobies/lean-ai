"""REST endpoints for explicit role-tuning prewarm operations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from lean_ai.llm.prompt_registry import PromptScope
from lean_ai.llm.role_tuning import (
    ensure_expert_role_tuning,
    ensure_primary_role_tuning,
    ensure_request_role_tuning,
    load_role_tuning_profile,
    apply_runtime_tuning_suggestions,
    profile_is_current,
    prompt_scope_for_role,
    role_tuning_profile_path,
    runtime_evaluation_is_current,
)
from lean_ai.routers.dependencies import expert_llm_client, llm_client, request_llm_client

role_tuning_router = APIRouter()


class PrewarmRoleTuningRequest(BaseModel):
    repo_root: str
    role: Literal["primary", "request", "expert"] | None = None


class ApplyRoleTuningSuggestionsRequest(BaseModel):
    repo_root: str
    role: Literal["primary", "request", "expert"]


class RoleTuningResult(BaseModel):
    role: str
    model_id: str
    status: str
    profile_path: str
    prompts_path: str
    selected_role_title: str | None = None
    runtime_reliability_score: int | None = None
    issues_found: list[str] = []
    suggestions_available: bool = False
    affected_prompt_keys: list[str] = []
    runtime_evaluation_status: str = "not_evaluated"
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
    if (
        existing is not None
        and profile_is_current(existing, scope, repo_root=repo_root)
        and runtime_evaluation_is_current(existing, scope, repo_root=repo_root)
    ):
        evaluation = existing.runtime_evaluation
        return RoleTuningResult(
            role=role,
            model_id=scope.model_id,
            status="skipped",
            profile_path=profile_path,
            prompts_path=prompts_path,
            selected_role_title=existing.selected_role_title,
            runtime_reliability_score=evaluation.reliability_score if evaluation else None,
            issues_found=evaluation.issues_found if evaluation else [],
            suggestions_available=evaluation.suggestions_available if evaluation else False,
            affected_prompt_keys=evaluation.affected_prompt_keys if evaluation else [],
            runtime_evaluation_status="current" if evaluation else "not_evaluated",
            warning=existing.judge_warning,
        )

    await ensure_fn()
    profile = load_role_tuning_profile(repo_root, scope)
    evaluation = profile.runtime_evaluation if profile is not None else None
    return RoleTuningResult(
        role=role,
        model_id=scope.model_id,
        status="tuned",
        profile_path=profile_path,
        prompts_path=prompts_path,
        selected_role_title=profile.selected_role_title if profile is not None else None,
        runtime_reliability_score=evaluation.reliability_score if evaluation else None,
        issues_found=evaluation.issues_found if evaluation else [],
        suggestions_available=evaluation.suggestions_available if evaluation else False,
        affected_prompt_keys=evaluation.affected_prompt_keys if evaluation else [],
        runtime_evaluation_status="current" if evaluation else "not_evaluated",
        warning=profile.judge_warning if profile is not None else None,
    )


def _role_bundle(role: str) -> tuple[PromptScope, object]:
    if role == "primary":
        return (
            prompt_scope_for_role(llm_client, "primary"),
            lambda repo_root: ensure_primary_role_tuning(
                repo_root=repo_root,
                assigned_client=llm_client,
                primary_client=llm_client,
                expert_client=expert_llm_client,
            ),
        )
    if role == "request":
        request_client = request_llm_client or llm_client
        return (
            prompt_scope_for_role(request_client, "request"),
            lambda repo_root: ensure_request_role_tuning(
                repo_root=repo_root,
                assigned_client=request_client,
                primary_client=llm_client,
                expert_client=expert_llm_client,
            ),
        )
    expert_client = expert_llm_client or llm_client
    return (
        prompt_scope_for_role(expert_client, "expert"),
        lambda repo_root: ensure_expert_role_tuning(
            repo_root=repo_root,
            assigned_client=expert_client,
            primary_client=llm_client,
            expert_client=expert_llm_client,
        ),
    )


@role_tuning_router.post("/role-tuning/prewarm", response_model=PrewarmRoleTuningResponse)
async def prewarm_role_tuning(
    request: PrewarmRoleTuningRequest,
) -> PrewarmRoleTuningResponse:
    """Prewarm sharable role-tuning artifacts for the active workspace."""
    repo_root = request.repo_root
    results: list[RoleTuningResult] = []

    roles = [request.role] if request.role else ["primary", "request", "expert"]
    for role in roles:
        scope, ensure_fn = _role_bundle(role)
        results.append(
            await _prewarm_one_role(
                repo_root=repo_root,
                role=role,
                scope=scope,
                ensure_fn=lambda ensure_fn=ensure_fn: ensure_fn(repo_root),
            )
        )

    return PrewarmRoleTuningResponse(results=results)


@role_tuning_router.post("/role-tuning/apply-suggestions", response_model=RoleTuningResult)
async def apply_role_tuning_suggestions(
    request: ApplyRoleTuningSuggestionsRequest,
) -> RoleTuningResult:
    repo_root = request.repo_root
    scope, ensure_fn = _role_bundle(request.role)
    await ensure_fn(repo_root)
    profile = load_role_tuning_profile(repo_root, scope)
    if profile is None:
        profile_path = str(role_tuning_profile_path(repo_root, scope))
        return RoleTuningResult(
            role=request.role,
            model_id=scope.model_id,
            status="missing_profile",
            profile_path=profile_path,
            prompts_path=str(Path(repo_root) / ".lean_ai" / "prompts.yaml"),
        )

    profile = apply_runtime_tuning_suggestions(repo_root, scope, profile)
    reloaded = load_role_tuning_profile(repo_root, scope) or profile
    evaluation = reloaded.runtime_evaluation
    return RoleTuningResult(
        role=request.role,
        model_id=scope.model_id,
        status="applied" if evaluation and evaluation.applied_at else "no_suggestions",
        profile_path=str(role_tuning_profile_path(repo_root, scope)),
        prompts_path=str(Path(repo_root) / ".lean_ai" / "prompts.yaml"),
        selected_role_title=reloaded.selected_role_title,
        runtime_reliability_score=evaluation.reliability_score if evaluation else None,
        issues_found=evaluation.issues_found if evaluation else [],
        suggestions_available=evaluation.suggestions_available if evaluation else False,
        affected_prompt_keys=evaluation.affected_prompt_keys if evaluation else [],
        runtime_evaluation_status="current" if evaluation else "not_evaluated",
        warning=reloaded.judge_warning,
    )
