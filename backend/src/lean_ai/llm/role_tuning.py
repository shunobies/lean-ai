"""Model-specific role tuning for Lean-AI agent roles."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ValidationError

from lean_ai.llm.prompt_registry import PromptScope, ScopedPromptOverride, registry
from lean_ai.llm.prompts import resolve_prompt_text

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)

ROLE_TUNING_SCHEMA_VERSION = 2
ROLE_TUNING_COMPOSITION_VERSION = "role-tuning-v3"
ROLE_TUNING_RUNTIME_EVALUATION_VERSION = "role-tuning-runtime-eval-v1"

REQUEST_ROLE = "request"
PRIMARY_ROLE = "primary"
EXPERT_ROLE = "expert"

GENERIC_JUDGE_CATEGORIES = (
    "clarity_and_communication",
    "task_alignment",
    "context_stewardship",
    "reasoning_quality",
    "premature_action_avoidance",
    "downstream_usefulness",
    "uncertainty_handling",
    "role_boundary_discipline",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "default"


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned)
    return deduped


@dataclass(frozen=True)
class RoleCalibrationConfig:
    """Configuration for calibrating a specific Lean-AI agent role."""

    agent_role: str
    work_summary_prompt_key: str
    summary_prompt_key: str
    prompt_keys: tuple[str, ...]
    candidate_titles: tuple[str, ...]
    probe_prompt_keys: tuple[str, str, str]
    tuning_header: str
    default_contract: tuple[str, ...]
    default_avoid: tuple[str, ...]
    severe_failure_fields: tuple[str, ...]


ROLE_CONFIGS: dict[str, RoleCalibrationConfig] = {
    REQUEST_ROLE: RoleCalibrationConfig(
        agent_role=REQUEST_ROLE,
        work_summary_prompt_key="role_tuning.request_work_summary",
        summary_prompt_key="role_tuning.request.summary",
        prompt_keys=("chat.system", "fix.request_system"),
        candidate_titles=(
            "Requirements Analyst",
            "Business Analyst",
            "Product Requirements Analyst",
            "Product Owner",
            "Project Manager",
            "Subject Matter Expert",
        ),
        probe_prompt_keys=(
            "role_tuning.request.probe.role_definition",
            "role_tuning.request.probe.scenario_judgment",
            "role_tuning.request.probe.boundary_discipline",
        ),
        tuning_header="MODEL-SPECIFIC REQUEST ROLE TUNING",
        default_contract=(
            "Use plain language with non-technical stakeholders.",
            "Gather user-facing goals before discussing implementation.",
            "Capture business goals, user workflow, constraints, edge cases, and success criteria.",
            "Ask a small number of focused clarification questions at a time.",
        ),
        default_avoid=(
            "Do not jump directly into architecture or framework selection.",
            "Do not invent business rules or hidden requirements.",
            "Do not ask stakeholders to define schemas, databases, or internal implementation details.",
        ),
        severe_failure_fields=(
            "premature_action_avoidance",
            "downstream_usefulness",
            "role_boundary_discipline",
        ),
    ),
    PRIMARY_ROLE: RoleCalibrationConfig(
        agent_role=PRIMARY_ROLE,
        work_summary_prompt_key="role_tuning.primary_work_summary",
        summary_prompt_key="role_tuning.primary.summary",
        prompt_keys=(
            "planning.scope_system",
            "planning.exploration_system",
            "execution.step_system",
            "execution.implementation_system",
            "fix.system",
        ),
        candidate_titles=(
            "Senior Software Engineer",
            "Implementation Engineer",
            "Feature Developer",
            "Codebase Maintainer",
            "Staff Software Engineer",
            "Software Engineer",
        ),
        probe_prompt_keys=(
            "role_tuning.primary.probe.role_definition",
            "role_tuning.primary.probe.scenario_judgment",
            "role_tuning.primary.probe.boundary_discipline",
        ),
        tuning_header="MODEL-SPECIFIC PRIMARY ROLE TUNING",
        default_contract=(
            "Execute the bounded task in front of you using the approved tools and scope limits.",
            "Verify names, signatures, file paths, and contracts against the real codebase before editing.",
            "Prefer small, coherent changes that satisfy the task and its checks.",
            "Use tests, lint, and command outputs to verify completion before claiming the work is done.",
        ),
        default_avoid=(
            "Do not expand the scope into broad refactors or architecture changes without justification.",
            "Do not invent file contents, signatures, or behaviors that have not been verified.",
            "Do not skip the step contract's success checks.",
        ),
        severe_failure_fields=(
            "task_alignment",
            "premature_action_avoidance",
            "downstream_usefulness",
            "role_boundary_discipline",
        ),
    ),
    EXPERT_ROLE: RoleCalibrationConfig(
        agent_role=EXPERT_ROLE,
        work_summary_prompt_key="role_tuning.expert_work_summary",
        summary_prompt_key="role_tuning.expert.summary",
        prompt_keys=(
            "planning.design_system",
            "planning.assembly_system",
            "planning.verification_system",
            "fix.system",
        ),
        candidate_titles=(
            "Software Architect",
            "Principal Engineer",
            "Systems Architect",
            "Technical Lead",
            "Staff Engineer",
            "Design Reviewer",
        ),
        probe_prompt_keys=(
            "role_tuning.expert.probe.role_definition",
            "role_tuning.expert.probe.scenario_judgment",
            "role_tuning.expert.probe.boundary_discipline",
        ),
        tuning_header="MODEL-SPECIFIC EXPERT ROLE TUNING",
        default_contract=(
            "Synthesize design, risks, interfaces, and verification strategy from the provided scope and evidence.",
            "Use file summaries and retrieved references as authoritative inputs instead of inventing codebase facts.",
            "Escalate uncertainty clearly and verify external dependencies when they are central to the task.",
            "Produce guidance that downstream implementation agents can execute without guessing your intent.",
        ),
        default_avoid=(
            "Do not fabricate file contents, signatures, or repository structure.",
            "Do not drift into implementation-level code writing when the role is design and planning.",
            "Do not treat uncertain assumptions as confirmed facts.",
        ),
        severe_failure_fields=(
            "task_alignment",
            "context_stewardship",
            "downstream_usefulness",
            "role_boundary_discipline",
        ),
    ),
}


class RoleDiscoveryResult(BaseModel):
    best_role_title: str
    alternate_role_titles: list[str] = []
    why_this_role_fits: str = ""
    required_behaviors: list[str] = []
    behaviors_to_avoid: list[str] = []
    risks_if_role_is_misunderstood: list[str] = []
    plain_language_role_contract: list[str] = []


class ProbeScore(BaseModel):
    clarity_and_communication: int = 0
    task_alignment: int = 0
    context_stewardship: int = 0
    reasoning_quality: int = 0
    premature_action_avoidance: int = 0
    downstream_usefulness: int = 0
    uncertainty_handling: int = 0
    role_boundary_discipline: int = 0
    notes: str = ""


class ProbeScoreSet(BaseModel):
    role_definition: ProbeScore
    scenario_judgment: ProbeScore
    boundary_discipline: ProbeScore


class JudgeEvaluation(BaseModel):
    agent_role: str
    candidate_role_title: str
    probe_scores: ProbeScoreSet
    raw_total_score: int = 0
    consistency_penalty: int = 0
    final_score: int = 0
    pass_status: str = "partial"
    major_strengths: list[str] = []
    major_risks: list[str] = []
    recommended_role_contract: list[str] = []
    recommended_prompt_override_guidance: str = ""


class AvoidRoleTitle(BaseModel):
    role_title: str
    reason: str


class CandidateResult(BaseModel):
    candidate_role_title: str
    raw_total_score: int
    consistency_penalty: int
    final_score: int
    pass_status: Literal["pass", "partial", "fail"]
    severe_role_boundary_failure: bool = False
    major_strengths: list[str] = []
    major_risks: list[str] = []
    recommended_role_contract: list[str] = []
    recommended_prompt_override_guidance: str = ""


class DerivedPromptGuidance(BaseModel):
    role_title: str
    required_behaviors: list[str] = []
    avoid_behaviors: list[str] = []
    prompt_override_guidance: str = ""


class RuntimePromptSummary(BaseModel):
    role_title: str
    required_behaviors: list[str] = []
    avoid_behaviors: list[str] = []
    prompt_override_guidance: str = ""


class RuntimeEvaluationScenarioSpec(BaseModel):
    name: str
    prompt_key: str
    user_message: str


class RuntimeScenarioEvaluation(BaseModel):
    scenario_name: str
    prompt_key: str
    score: int = 0
    pass_status: Literal["pass", "partial", "fail"] = "partial"
    issues: list[str] = []
    suggested_adjustments: list[str] = []
    notes: str = ""


class RuntimeEvaluationSynthesis(BaseModel):
    reliability_score: int = 0
    issues_found: list[str] = []
    suggestions_available: bool = False
    affected_prompt_keys: list[str] = []
    suggestion_summary: list[str] = []
    required_behaviors: list[str] = []
    avoid_behaviors: list[str] = []
    prompt_override_guidance: str = ""


class RuntimePromptEvaluation(BaseModel):
    version_hash: str
    evaluated_at: str
    scenarios_run: list[str] = []
    reliability_score: int = 0
    issues_found: list[str] = []
    affected_prompt_keys: list[str] = []
    scenario_results: list[RuntimeScenarioEvaluation] = []
    suggestions_available: bool = False
    suggestion_summary: list[str] = []
    suggested_runtime_prompt_summary: RuntimePromptSummary | None = None
    applied_at: str | None = None


RUNTIME_EVAL_SCENARIOS: dict[str, tuple[RuntimeEvaluationScenarioSpec, ...]] = {
    REQUEST_ROLE: (
        RuntimeEvaluationScenarioSpec(
            name="clarification_discipline",
            prompt_key="chat.system",
            user_message=(
                "I need a feature where users upload receipts and the system tells them what to do next. "
                "Please help me figure out the right requirements without jumping into implementation."
            ),
        ),
        RuntimeEvaluationScenarioSpec(
            name="anti_architecture_boundary",
            prompt_key="fix.request_system",
            user_message=(
                "The stakeholder insists engineering must use PostgreSQL, Redis, and React. "
                "Handle this as the request-role agent without accepting those implementation choices as final."
            ),
        ),
    ),
    PRIMARY_ROLE: (
        RuntimeEvaluationScenarioSpec(
            name="bounded_execution",
            prompt_key="execution.step_system",
            user_message=(
                "You have an approved step: add server-side validation to the existing POST /api/orders handler, "
                "update affected tests, and verify the change. Explain your execution approach."
            ),
        ),
        RuntimeEvaluationScenarioSpec(
            name="verification_discipline",
            prompt_key="execution.implementation_system",
            user_message=(
                "The implementation is complete but the test command is unavailable in this environment. "
                "Describe how you should report the result without overstating success."
            ),
        ),
        RuntimeEvaluationScenarioSpec(
            name="scope_control",
            prompt_key="fix.system",
            user_message=(
                "While fixing a small approved bug, someone asks you to redesign the whole order architecture too. "
                "Respond with the scope discipline expected from this role."
            ),
        ),
    ),
    EXPERT_ROLE: (
        RuntimeEvaluationScenarioSpec(
            name="evidence_discipline",
            prompt_key="planning.design_system",
            user_message=(
                "The file summary suggests one integration pattern, but a key dependency detail is missing. "
                "Describe how you would proceed without fabricating repository facts."
            ),
        ),
        RuntimeEvaluationScenarioSpec(
            name="downstream_usefulness",
            prompt_key="planning.assembly_system",
            user_message=(
                "A feature request needs background document processing, status tracking, retry behavior, and "
                "a progress indicator. Describe the design and handoff work before implementation."
            ),
        ),
        RuntimeEvaluationScenarioSpec(
            name="verification_strategy",
            prompt_key="planning.verification_system",
            user_message=(
                "The plan modifies behavior across API validation and retry logic. Outline the verification focus "
                "areas while escalating uncertainty where evidence is incomplete."
            ),
        ),
    ),
}


class RoleTuningProfile(BaseModel):
    schema_version: int = ROLE_TUNING_SCHEMA_VERSION
    created_at: str
    updated_at: str
    agent_role: str
    assigned_model: str
    judge_model: str
    judge_role: str
    work_summary_hash: str
    prompt_version_hash: str
    selected_role_title: str
    alternate_role_titles: list[str] = []
    avoid_role_titles: list[AvoidRoleTitle] = []
    role_discovery: RoleDiscoveryResult
    candidate_results: list[CandidateResult] = []
    approved_role_contract: list[str] = []
    derived_prompt_guidance: DerivedPromptGuidance
    runtime_prompt_summary: RuntimePromptSummary
    runtime_evaluation: RuntimePromptEvaluation | None = None
    judge_warning: str | None = None


@dataclass(frozen=True)
class JudgeSelection:
    client: "LLMClient"
    judge_role: str
    warning: str | None = None


def model_identity(client: "LLMClient") -> str:
    """Return a stable provider-qualified identity for a client."""
    provider = getattr(client, "provider_name", "") or "unknown"
    model = getattr(client, "model_name", "") or "unknown"
    return f"{provider}:{model}"


def prompt_scope_for_role(client: "LLMClient", agent_role: str) -> PromptScope:
    """Return the registry scope for a model-role pair."""
    return PromptScope(model_id=model_identity(client), agent_role=agent_role)


def request_prompt_scope(client: "LLMClient") -> PromptScope:
    """Backward-compatible helper for the request role."""
    return prompt_scope_for_role(client, REQUEST_ROLE)


def role_tuning_profile_path(repo_root: str, scope: PromptScope) -> Path:
    """Return the JSON profile path for a model-role tuning result."""
    model_slug = _slugify(scope.model_id)
    role_slug = _slugify(scope.agent_role)
    return Path(repo_root) / ".lean_ai" / "role_tuning" / f"{role_slug}--{model_slug}.json"


def _role_config(agent_role: str) -> RoleCalibrationConfig:
    config = ROLE_CONFIGS.get(agent_role)
    if config is None:
        raise KeyError(f"Unsupported role tuning agent role: {agent_role!r}")
    return config


def choose_judge_client(
    *,
    agent_role: str,
    assigned_client: "LLMClient",
    primary_client: "LLMClient",
    expert_client: "LLMClient | None" = None,
) -> JudgeSelection:
    """Choose the strongest available judge model for role tuning."""
    assigned_id = model_identity(assigned_client)
    expert_id = model_identity(expert_client) if expert_client is not None else None
    primary_id = model_identity(primary_client)

    if agent_role != EXPERT_ROLE and expert_client is not None and expert_id != assigned_id:
        return JudgeSelection(expert_client, EXPERT_ROLE)
    if primary_id != assigned_id:
        return JudgeSelection(primary_client, PRIMARY_ROLE)
    if expert_client is not None and expert_id != assigned_id:
        return JudgeSelection(expert_client, EXPERT_ROLE)
    return JudgeSelection(
        assigned_client,
        agent_role,
        warning=(
            "Role tuning was judged by the same model being calibrated because "
            "no stronger external judge model was available."
        ),
    )


def role_work_summary(agent_role: str, repo_root: str | None = None) -> str:
    """Return the canonical work summary for a role."""
    if repo_root:
        registry.load(repo_root)
    return registry.get_text(_role_config(agent_role).work_summary_prompt_key)


def request_work_summary(repo_root: str | None = None) -> str:
    return role_work_summary(REQUEST_ROLE, repo_root)


def role_work_summary_hash(agent_role: str, repo_root: str | None = None) -> str:
    """Return the current work summary hash for a role."""
    return _sha256_text(role_work_summary(agent_role, repo_root))


def request_work_summary_hash(repo_root: str | None = None) -> str:
    return role_work_summary_hash(REQUEST_ROLE, repo_root)


def role_prompt_version_hash(agent_role: str, repo_root: str | None = None) -> str:
    """Return a hash that invalidates tuning when a role's prompts change."""
    if repo_root:
        registry.load(repo_root)
    config = _role_config(agent_role)
    tuned_prompt_text = "\n".join(resolve_prompt_text(prompt_key) for prompt_key in config.prompt_keys)
    tuning_prompts = "\n".join(
        [
            registry.get_text("role_tuning.discovery"),
            registry.get_text(config.probe_prompt_keys[0]),
            registry.get_text(config.probe_prompt_keys[1]),
            registry.get_text(config.probe_prompt_keys[2]),
            registry.get_text("role_tuning.judge"),
            registry.get_text(config.summary_prompt_key),
        ]
    )
    return _sha256_text(
        ROLE_TUNING_COMPOSITION_VERSION,
        agent_role,
        tuned_prompt_text,
        tuning_prompts,
    )


def role_runtime_evaluation_version_hash(
    agent_role: str,
    scope: PromptScope,
    repo_root: str | None = None,
) -> str:
    """Return a hash that invalidates runtime evaluation when prompt surfaces change."""
    if repo_root:
        registry.load(repo_root)
    config = _role_config(agent_role)
    scenario_text = "\n".join(
        f"{scenario.prompt_key}:{scenario.name}:{scenario.user_message}"
        for scenario in RUNTIME_EVAL_SCENARIOS.get(agent_role, ())
    )
    resolved_surfaces = "\n".join(
        f"{prompt_key}\n{resolve_prompt_text(prompt_key, scope=scope)}"
        for prompt_key in config.prompt_keys
    )
    return _sha256_text(
        ROLE_TUNING_RUNTIME_EVALUATION_VERSION,
        agent_role,
        scope.model_id,
        scenario_text,
        resolved_surfaces,
    )


def request_prompt_version_hash(repo_root: str | None = None) -> str:
    return role_prompt_version_hash(REQUEST_ROLE, repo_root)


def runtime_evaluation_is_current(
    profile: RoleTuningProfile,
    scope: PromptScope,
    *,
    repo_root: str | None = None,
) -> bool:
    evaluation = profile.runtime_evaluation
    if evaluation is None:
        return False
    return evaluation.version_hash == role_runtime_evaluation_version_hash(
        scope.agent_role,
        scope,
        repo_root,
    )


def load_role_tuning_profile(repo_root: str, scope: PromptScope) -> RoleTuningProfile | None:
    """Load a persisted role-tuning profile if present and valid."""
    path = role_tuning_profile_path(repo_root, scope)
    if not path.exists():
        return None
    try:
        return RoleTuningProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError):
        logger.warning("Ignoring invalid role tuning profile at %s", path, exc_info=True)
        return None


def profile_is_current(
    profile: RoleTuningProfile,
    scope: PromptScope,
    *,
    repo_root: str | None = None,
) -> bool:
    """Return True when a profile still matches the current role inputs."""
    return (
        profile.schema_version == ROLE_TUNING_SCHEMA_VERSION
        and profile.agent_role == scope.agent_role
        and profile.assigned_model == scope.model_id
        and profile.work_summary_hash == role_work_summary_hash(scope.agent_role, repo_root)
        and profile.prompt_version_hash == role_prompt_version_hash(scope.agent_role, repo_root)
    )


def _persist_role_tuning_profile(repo_root: str, scope: PromptScope, profile: RoleTuningProfile) -> None:
    path = role_tuning_profile_path(repo_root, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _score_probe(probe: ProbeScore) -> int:
    return sum(int(getattr(probe, category)) for category in GENERIC_JUDGE_CATEGORIES)


def _candidate_pass_status(raw_total_score: int, consistency_penalty: int) -> Literal["pass", "partial", "fail"]:
    if raw_total_score >= 40 and consistency_penalty <= 1:
        return "pass"
    if raw_total_score >= 32 and consistency_penalty <= 3:
        return "partial"
    return "fail"


def _is_severe_role_boundary_failure(
    config: RoleCalibrationConfig,
    judged: JudgeEvaluation,
) -> bool:
    probe_scores = (
        judged.probe_scores.role_definition,
        judged.probe_scores.scenario_judgment,
        judged.probe_scores.boundary_discipline,
    )
    for probe in probe_scores:
        for field in config.severe_failure_fields:
            if int(getattr(probe, field, 0)) == 0:
                return True
    return False


def _to_candidate_result(
    config: RoleCalibrationConfig,
    judged: JudgeEvaluation,
) -> CandidateResult:
    recomputed_raw = (
        _score_probe(judged.probe_scores.role_definition)
        + _score_probe(judged.probe_scores.scenario_judgment)
        + _score_probe(judged.probe_scores.boundary_discipline)
    )
    consistency_penalty = max(0, int(judged.consistency_penalty))
    final_score = recomputed_raw - consistency_penalty
    return CandidateResult(
        candidate_role_title=judged.candidate_role_title,
        raw_total_score=recomputed_raw,
        consistency_penalty=consistency_penalty,
        final_score=final_score,
        pass_status=_candidate_pass_status(recomputed_raw, consistency_penalty),
        severe_role_boundary_failure=_is_severe_role_boundary_failure(config, judged),
        major_strengths=judged.major_strengths,
        major_risks=judged.major_risks,
        recommended_role_contract=judged.recommended_role_contract,
        recommended_prompt_override_guidance=judged.recommended_prompt_override_guidance,
    )


def _choose_best_candidate(candidates: list[CandidateResult]) -> CandidateResult:
    viable = [candidate for candidate in candidates if not candidate.severe_role_boundary_failure]
    pool = viable or candidates
    status_rank = {"pass": 2, "partial": 1, "fail": 0}
    return max(
        pool,
        key=lambda candidate: (
            status_rank.get(candidate.pass_status, 0),
            candidate.final_score,
            candidate.raw_total_score,
        ),
    )


def _build_approved_role_contract(
    config: RoleCalibrationConfig,
    discovery: RoleDiscoveryResult,
    selected_candidate: CandidateResult,
) -> list[str]:
    contract = _dedupe_preserve_order(
        selected_candidate.recommended_role_contract
        + discovery.plain_language_role_contract
        + discovery.required_behaviors
    )
    if contract:
        return contract
    return list(config.default_contract)


def _build_avoid_role_titles(candidates: list[CandidateResult], selected_title: str) -> list[AvoidRoleTitle]:
    avoid: list[AvoidRoleTitle] = []
    for candidate in candidates:
        if candidate.candidate_role_title == selected_title:
            continue
        if candidate.major_risks:
            reason = candidate.major_risks[0]
        elif candidate.severe_role_boundary_failure:
            reason = "Severe role-boundary failure during role calibration."
        else:
            reason = "Lower-scoring role framing than the selected role title."
        avoid.append(AvoidRoleTitle(role_title=candidate.candidate_role_title, reason=reason))
    return avoid


def _build_derived_prompt_guidance(
    config: RoleCalibrationConfig,
    profile: RoleTuningProfile | None = None,
    *,
    selected_role_title: str | None = None,
    approved_role_contract: list[str] | None = None,
    discovery: RoleDiscoveryResult | None = None,
    selected_candidate: CandidateResult | None = None,
) -> DerivedPromptGuidance:
    if profile is not None:
        return profile.derived_prompt_guidance
    assert selected_role_title is not None
    assert approved_role_contract is not None
    assert discovery is not None
    assert selected_candidate is not None
    avoid_behaviors = _dedupe_preserve_order(
        discovery.behaviors_to_avoid
        + discovery.risks_if_role_is_misunderstood
        + selected_candidate.major_risks
        + list(config.default_avoid)
    )
    return DerivedPromptGuidance(
        role_title=selected_role_title,
        required_behaviors=approved_role_contract,
        avoid_behaviors=avoid_behaviors,
        prompt_override_guidance=selected_candidate.recommended_prompt_override_guidance,
    )


def _build_scoped_prompt_override_text(
    config: RoleCalibrationConfig,
    base_text: str,
    profile: RoleTuningProfile,
) -> str:
    guidance = profile.runtime_prompt_summary
    required = "\n".join(f"- {item}" for item in guidance.required_behaviors)
    avoid = "\n".join(f"- {item}" for item in guidance.avoid_behaviors)
    extra = guidance.prompt_override_guidance.strip()
    extra_block = f"\nAdditional prompt guidance:\n{extra}\n" if extra else ""
    tuning_block = (
        f"{config.tuning_header}:\n"
        f"- Active role framing: {guidance.role_title}\n"
        "- This role title was empirically selected for the active model.\n"
        "- Treat the following contract as mandatory.\n\n"
        "Required behaviors:\n"
        f"{required}\n\n"
        "Behaviors to avoid:\n"
        f"{avoid}\n"
        f"{extra_block}\n"
    )
    return f"{tuning_block}\n{base_text}"


def _role_prompt_surfaces(config: RoleCalibrationConfig, *, scope: PromptScope | None = None) -> str:
    surfaces: list[str] = []
    for prompt_key in config.prompt_keys:
        prompt_text = resolve_prompt_text(prompt_key, scope=scope)
        surfaces.append(f"Prompt key: {prompt_key}\n{prompt_text}")
    return "\n\n".join(surfaces)


def ensure_role_scoped_overrides(
    repo_root: str,
    scope: PromptScope,
    profile: RoleTuningProfile,
) -> None:
    """Persist the scoped prompt overrides derived from a tuning profile."""
    config = _role_config(scope.agent_role)
    overrides = [
        ScopedPromptOverride(
            prompt_key=prompt_key,
            model_id=scope.model_id,
            agent_role=scope.agent_role,
            text=_build_scoped_prompt_override_text(config, registry.get_text(prompt_key), profile),
        )
        for prompt_key in config.prompt_keys
    ]
    registry.save_scoped_overrides(repo_root, overrides)


def ensure_request_role_scoped_overrides(
    repo_root: str,
    scope: PromptScope,
    profile: RoleTuningProfile,
) -> None:
    ensure_role_scoped_overrides(repo_root, scope, profile)


async def _run_role_discovery(
    assigned_client: "LLMClient",
    *,
    work_summary: str,
) -> RoleDiscoveryResult:
    prompt = registry.format_text("role_tuning.discovery", WORK_SUMMARY=work_summary)
    messages = [
        {"role": "system", "content": "Return JSON only. Do not start the real task."},
        {"role": "user", "content": prompt},
    ]
    return await assigned_client.chat_structured(messages, RoleDiscoveryResult)


async def _run_probe(
    assigned_client: "LLMClient",
    *,
    prompt_key: str,
    candidate_role_title: str,
    work_summary: str,
) -> str:
    prompt = registry.format_text(
        prompt_key,
        CANDIDATE_ROLE_TITLE=candidate_role_title,
        WORK_SUMMARY=work_summary,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Follow the requested role framing faithfully. "
                "Do not return JSON. Answer in concise natural language."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    return await assigned_client.chat_raw(messages)


async def _judge_candidate(
    judge_client: "LLMClient",
    *,
    agent_role: str,
    candidate_role_title: str,
    work_summary: str,
    probe_a_response: str,
    probe_b_response: str,
    probe_c_response: str,
) -> JudgeEvaluation:
    prompt = registry.format_text(
        "role_tuning.judge",
        AGENT_ROLE=agent_role,
        CANDIDATE_ROLE_TITLE=candidate_role_title,
        WORK_SUMMARY=work_summary,
        PROBE_A_RESPONSE=probe_a_response,
        PROBE_B_RESPONSE=probe_b_response,
        PROBE_C_RESPONSE=probe_c_response,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Return JSON only. Score the candidate role title using the response schema "
                "categories and the work summary alignment."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    return await judge_client.chat_structured(messages, JudgeEvaluation)


async def _summarize_runtime_prompt(
    judge_client: "LLMClient",
    *,
    config: RoleCalibrationConfig,
    scope: PromptScope,
    work_summary: str,
    discovery: RoleDiscoveryResult,
    selected_candidate: CandidateResult,
    approved_role_contract: list[str],
) -> RuntimePromptSummary:
    prompt = registry.format_text(
        config.summary_prompt_key,
        AGENT_ROLE=config.agent_role,
        WORK_SUMMARY=work_summary,
        ROLE_PROMPT_SURFACES=_role_prompt_surfaces(config, scope=scope),
        SELECTED_ROLE_TITLE=selected_candidate.candidate_role_title,
        APPROVED_ROLE_CONTRACT="\n".join(f"- {item}" for item in approved_role_contract),
        CANDIDATE_STRENGTHS="\n".join(f"- {item}" for item in selected_candidate.major_strengths),
        CANDIDATE_RISKS="\n".join(f"- {item}" for item in selected_candidate.major_risks),
        CANDIDATE_PROMPT_GUIDANCE=selected_candidate.recommended_prompt_override_guidance,
        DISCOVERY_BEST_ROLE_TITLE=discovery.best_role_title,
        DISCOVERY_ALTERNATE_ROLE_TITLES="\n".join(f"- {item}" for item in discovery.alternate_role_titles),
        DISCOVERY_ROLE_CONTRACT="\n".join(f"- {item}" for item in discovery.plain_language_role_contract),
        DISCOVERY_REQUIRED_BEHAVIORS="\n".join(f"- {item}" for item in discovery.required_behaviors),
        DISCOVERY_BEHAVIORS_TO_AVOID="\n".join(f"- {item}" for item in discovery.behaviors_to_avoid),
        DISCOVERY_MISUNDERSTOOD_RISKS="\n".join(f"- {item}" for item in discovery.risks_if_role_is_misunderstood),
        DEFAULT_AVOID_BEHAVIORS="\n".join(f"- {item}" for item in config.default_avoid),
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Return JSON only. Produce a compact runtime prompt summary that improves "
                "the tuned prompt surfaces without contradicting them."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    summary = await judge_client.chat_structured(messages, RuntimePromptSummary)
    return RuntimePromptSummary(
        role_title=summary.role_title.strip() or selected_candidate.candidate_role_title,
        required_behaviors=_dedupe_preserve_order(summary.required_behaviors),
        avoid_behaviors=_dedupe_preserve_order(summary.avoid_behaviors),
        prompt_override_guidance=summary.prompt_override_guidance.strip(),
    )


async def _run_runtime_scenario(
    assigned_client: "LLMClient",
    *,
    prompt_text: str,
    scenario: RuntimeEvaluationScenarioSpec,
) -> str:
    messages = [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": scenario.user_message},
    ]
    return await assigned_client.chat_raw(messages)


async def _judge_runtime_scenario(
    judge_client: "LLMClient",
    *,
    agent_role: str,
    scope: PromptScope,
    scenario: RuntimeEvaluationScenarioSpec,
    prompt_text: str,
    assistant_response: str,
) -> RuntimeScenarioEvaluation:
    prompt = (
        "You are evaluating whether a runtime-tuned Lean-AI prompt causes correct role behavior.\n\n"
        f"Agent role: {agent_role}\n"
        f"Assigned model: {scope.model_id}\n"
        f"Prompt key: {scenario.prompt_key}\n"
        f"Scenario name: {scenario.name}\n\n"
        "Resolved system prompt shown to the model:\n"
        f"{prompt_text}\n\n"
        "Scenario user message:\n"
        f"{scenario.user_message}\n\n"
        "Model response:\n"
        f"{assistant_response}\n\n"
        "Score the response from 0-5 for prompt reliability. Return JSON only.\n"
        "- pass: strong adherence to role boundary and prompt contract\n"
        "- partial: mostly correct but missing an important guardrail\n"
        "- fail: contradicts or meaningfully weakens the prompt contract\n"
        "Be concrete about issues and suggested adjustments."
    )
    messages = [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": prompt},
    ]
    return await judge_client.chat_structured(messages, RuntimeScenarioEvaluation)


async def _synthesize_runtime_evaluation(
    judge_client: "LLMClient",
    *,
    config: RoleCalibrationConfig,
    scope: PromptScope,
    profile: RoleTuningProfile,
    scenario_results: list[RuntimeScenarioEvaluation],
) -> RuntimeEvaluationSynthesis:
    scenarios_text = "\n\n".join(
        [
            (
                f"Scenario: {result.scenario_name}\n"
                f"Prompt key: {result.prompt_key}\n"
                f"Score: {result.score}\n"
                f"Pass status: {result.pass_status}\n"
                f"Issues:\n" + "\n".join(f"- {item}" for item in result.issues) + "\n"
                f"Suggested adjustments:\n"
                + "\n".join(f"- {item}" for item in result.suggested_adjustments)
            ).strip()
            for result in scenario_results
        ]
    )
    prompt = (
        "You are summarizing runtime prompt evaluation results for a tuned Lean-AI role.\n\n"
        f"Agent role: {config.agent_role}\n"
        f"Assigned model: {scope.model_id}\n"
        f"Selected role title: {profile.selected_role_title}\n\n"
        "Current runtime prompt summary:\n"
        f"Role title: {profile.runtime_prompt_summary.role_title}\n"
        f"Required behaviors:\n{chr(10).join(f'- {item}' for item in profile.runtime_prompt_summary.required_behaviors)}\n"
        f"Avoid behaviors:\n{chr(10).join(f'- {item}' for item in profile.runtime_prompt_summary.avoid_behaviors)}\n"
        f"Prompt guidance:\n{profile.runtime_prompt_summary.prompt_override_guidance}\n\n"
        "Current scoped prompt surfaces:\n"
        f"{_role_prompt_surfaces(config, scope=scope)}\n\n"
        "Scenario evaluation results:\n"
        f"{scenarios_text}\n\n"
        "Return JSON only. Produce a compact synthesis with:\n"
        "- reliability_score from 0-100\n"
        "- issues_found as a short de-duplicated list\n"
        "- suggestions_available true only when meaningful runtime prompt improvements are warranted\n"
        "- affected_prompt_keys limited to the prompt surfaces that need help\n"
        "- suggestion_summary as concise user-facing bullets\n"
        "- required_behaviors / avoid_behaviors / prompt_override_guidance only when suggestions are warranted\n"
        "Keep suggestions minimal and preserve the existing product intent."
    )
    messages = [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": prompt},
    ]
    synthesis = await judge_client.chat_structured(messages, RuntimeEvaluationSynthesis)
    return RuntimeEvaluationSynthesis(
        reliability_score=max(0, min(100, int(synthesis.reliability_score))),
        issues_found=_dedupe_preserve_order(synthesis.issues_found),
        suggestions_available=bool(synthesis.suggestions_available),
        affected_prompt_keys=_dedupe_preserve_order(
            [
                key
                for key in synthesis.affected_prompt_keys
                if key in config.prompt_keys
            ]
        ),
        suggestion_summary=_dedupe_preserve_order(synthesis.suggestion_summary),
        required_behaviors=_dedupe_preserve_order(synthesis.required_behaviors),
        avoid_behaviors=_dedupe_preserve_order(synthesis.avoid_behaviors),
        prompt_override_guidance=synthesis.prompt_override_guidance.strip(),
    )


async def evaluate_runtime_prompt_reliability(
    *,
    repo_root: str,
    scope: PromptScope,
    profile: RoleTuningProfile,
    assigned_client: "LLMClient",
    judge_client: "LLMClient",
) -> RoleTuningProfile:
    """Evaluate the actual resolved prompt surfaces for a tuned role/model pair."""
    registry.load(repo_root)
    config = _role_config(scope.agent_role)
    scenario_results: list[RuntimeScenarioEvaluation] = []
    for scenario in RUNTIME_EVAL_SCENARIOS.get(scope.agent_role, ()):
        prompt_text = resolve_prompt_text(scenario.prompt_key, scope=scope)
        assistant_response = await _run_runtime_scenario(
            assigned_client,
            prompt_text=prompt_text,
            scenario=scenario,
        )
        result = await _judge_runtime_scenario(
            judge_client,
            agent_role=scope.agent_role,
            scope=scope,
            scenario=scenario,
            prompt_text=prompt_text,
            assistant_response=assistant_response,
        )
        scenario_results.append(
            RuntimeScenarioEvaluation(
                scenario_name=scenario.name,
                prompt_key=scenario.prompt_key,
                score=max(0, min(5, int(result.score))),
                pass_status=result.pass_status,
                issues=_dedupe_preserve_order(result.issues),
                suggested_adjustments=_dedupe_preserve_order(result.suggested_adjustments),
                notes=result.notes.strip(),
            )
        )

    synthesis = await _synthesize_runtime_evaluation(
        judge_client,
        config=config,
        scope=scope,
        profile=profile,
        scenario_results=scenario_results,
    )
    suggested_summary: RuntimePromptSummary | None = None
    if synthesis.suggestions_available:
        suggested_summary = RuntimePromptSummary(
            role_title=profile.runtime_prompt_summary.role_title,
            required_behaviors=synthesis.required_behaviors or profile.runtime_prompt_summary.required_behaviors,
            avoid_behaviors=synthesis.avoid_behaviors or profile.runtime_prompt_summary.avoid_behaviors,
            prompt_override_guidance=(
                synthesis.prompt_override_guidance
                or profile.runtime_prompt_summary.prompt_override_guidance
            ),
        )

    updated = profile.model_copy(deep=True)
    updated.updated_at = _now_iso()
    updated.runtime_evaluation = RuntimePromptEvaluation(
        version_hash=role_runtime_evaluation_version_hash(scope.agent_role, scope, repo_root),
        evaluated_at=_now_iso(),
        scenarios_run=[result.scenario_name for result in scenario_results],
        reliability_score=synthesis.reliability_score,
        issues_found=synthesis.issues_found,
        affected_prompt_keys=synthesis.affected_prompt_keys or [result.prompt_key for result in scenario_results if result.pass_status != "pass"],
        scenario_results=scenario_results,
        suggestions_available=synthesis.suggestions_available,
        suggestion_summary=synthesis.suggestion_summary,
        suggested_runtime_prompt_summary=suggested_summary,
    )
    _persist_role_tuning_profile(repo_root, scope, updated)
    return updated


def apply_runtime_tuning_suggestions(
    repo_root: str,
    scope: PromptScope,
    profile: RoleTuningProfile,
) -> RoleTuningProfile:
    """Apply approved runtime prompt suggestions to the scoped overrides only."""
    evaluation = profile.runtime_evaluation
    if evaluation is None or not evaluation.suggestions_available or evaluation.suggested_runtime_prompt_summary is None:
        return profile

    updated = profile.model_copy(deep=True)
    updated.updated_at = _now_iso()
    updated.runtime_prompt_summary = evaluation.suggested_runtime_prompt_summary
    updated.derived_prompt_guidance = DerivedPromptGuidance(
        role_title=updated.runtime_prompt_summary.role_title,
        required_behaviors=updated.runtime_prompt_summary.required_behaviors,
        avoid_behaviors=updated.runtime_prompt_summary.avoid_behaviors,
        prompt_override_guidance=updated.runtime_prompt_summary.prompt_override_guidance,
    )
    updated.runtime_evaluation.applied_at = _now_iso()
    ensure_role_scoped_overrides(repo_root, scope, updated)
    _persist_role_tuning_profile(repo_root, scope, updated)
    return updated


async def calibrate_role(
    *,
    repo_root: str,
    agent_role: str,
    assigned_client: "LLMClient",
    primary_client: "LLMClient",
    expert_client: "LLMClient | None" = None,
) -> RoleTuningProfile:
    """Run role tuning for the assigned model and persist the result."""
    config = _role_config(agent_role)
    work_summary = role_work_summary(agent_role, repo_root)
    discovery = await _run_role_discovery(assigned_client, work_summary=work_summary)
    candidate_titles = _dedupe_preserve_order(
        list(config.candidate_titles) + [discovery.best_role_title] + discovery.alternate_role_titles
    )
    judge = choose_judge_client(
        agent_role=agent_role,
        assigned_client=assigned_client,
        primary_client=primary_client,
        expert_client=expert_client,
    )

    candidate_results: list[CandidateResult] = []
    for title in candidate_titles:
        probe_a = await _run_probe(
            assigned_client,
            prompt_key=config.probe_prompt_keys[0],
            candidate_role_title=title,
            work_summary=work_summary,
        )
        probe_b = await _run_probe(
            assigned_client,
            prompt_key=config.probe_prompt_keys[1],
            candidate_role_title=title,
            work_summary=work_summary,
        )
        probe_c = await _run_probe(
            assigned_client,
            prompt_key=config.probe_prompt_keys[2],
            candidate_role_title=title,
            work_summary=work_summary,
        )
        judged = await _judge_candidate(
            judge.client,
            agent_role=agent_role,
            candidate_role_title=title,
            work_summary=work_summary,
            probe_a_response=probe_a,
            probe_b_response=probe_b,
            probe_c_response=probe_c,
        )
        candidate_results.append(_to_candidate_result(config, judged))

    selected_candidate = _choose_best_candidate(candidate_results)
    selected_role_title = selected_candidate.candidate_role_title
    approved_role_contract = _build_approved_role_contract(config, discovery, selected_candidate)
    derived_guidance = _build_derived_prompt_guidance(
        config,
        selected_role_title=selected_role_title,
        approved_role_contract=approved_role_contract,
        discovery=discovery,
        selected_candidate=selected_candidate,
    )
    runtime_prompt_summary = await _summarize_runtime_prompt(
        judge.client,
        config=config,
        scope=prompt_scope_for_role(assigned_client, agent_role),
        work_summary=work_summary,
        discovery=discovery,
        selected_candidate=selected_candidate,
        approved_role_contract=approved_role_contract,
    )
    alternate_titles = _dedupe_preserve_order(
        [title for title in candidate_titles if title.casefold() != selected_role_title.casefold()]
    )
    scope = prompt_scope_for_role(assigned_client, agent_role)
    now = _now_iso()
    profile = RoleTuningProfile(
        created_at=now,
        updated_at=now,
        agent_role=agent_role,
        assigned_model=scope.model_id,
        judge_model=model_identity(judge.client),
        judge_role=judge.judge_role,
        work_summary_hash=role_work_summary_hash(agent_role, repo_root),
        prompt_version_hash=role_prompt_version_hash(agent_role, repo_root),
        selected_role_title=selected_role_title,
        alternate_role_titles=alternate_titles,
        avoid_role_titles=_build_avoid_role_titles(candidate_results, selected_role_title),
        role_discovery=discovery,
        candidate_results=candidate_results,
        approved_role_contract=approved_role_contract,
        derived_prompt_guidance=derived_guidance,
        runtime_prompt_summary=runtime_prompt_summary,
        judge_warning=judge.warning,
    )
    _persist_role_tuning_profile(repo_root, scope, profile)
    ensure_role_scoped_overrides(repo_root, scope, profile)
    return profile


async def calibrate_request_role(
    *,
    repo_root: str,
    assigned_client: "LLMClient",
    primary_client: "LLMClient",
    expert_client: "LLMClient | None" = None,
) -> RoleTuningProfile:
    return await calibrate_role(
        repo_root=repo_root,
        agent_role=REQUEST_ROLE,
        assigned_client=assigned_client,
        primary_client=primary_client,
        expert_client=expert_client,
    )


async def ensure_role_tuning(
    *,
    repo_root: str | None,
    agent_role: str,
    assigned_client: "LLMClient",
    primary_client: "LLMClient",
    expert_client: "LLMClient | None" = None,
) -> PromptScope | None:
    """Ensure a current tuning profile and scoped prompt overrides exist."""
    if not repo_root:
        return None

    registry.load(repo_root)
    scope = prompt_scope_for_role(assigned_client, agent_role)
    profile = load_role_tuning_profile(repo_root, scope)
    judge = choose_judge_client(
        agent_role=agent_role,
        assigned_client=assigned_client,
        primary_client=primary_client,
        expert_client=expert_client,
    )

    try:
        if profile is None or not profile_is_current(profile, scope, repo_root=repo_root):
            profile = await calibrate_role(
                repo_root=repo_root,
                agent_role=agent_role,
                assigned_client=assigned_client,
                primary_client=primary_client,
                expert_client=expert_client,
            )
        if profile is not None and not runtime_evaluation_is_current(profile, scope, repo_root=repo_root):
            profile = await evaluate_runtime_prompt_reliability(
                repo_root=repo_root,
                scope=scope,
                profile=profile,
                assigned_client=assigned_client,
                judge_client=judge.client,
            )
        if profile is not None:
            ensure_role_scoped_overrides(repo_root, scope, profile)
            registry.load(repo_root)
            return scope
    except Exception:
        logger.exception("Role tuning failed for %s (%s)", scope.agent_role, scope.model_id)
        registry.load(repo_root)
        return None

    registry.load(repo_root)
    return scope


async def ensure_request_role_tuning(
    *,
    repo_root: str | None,
    assigned_client: "LLMClient",
    primary_client: "LLMClient",
    expert_client: "LLMClient | None" = None,
) -> PromptScope | None:
    return await ensure_role_tuning(
        repo_root=repo_root,
        agent_role=REQUEST_ROLE,
        assigned_client=assigned_client,
        primary_client=primary_client,
        expert_client=expert_client,
    )


async def ensure_primary_role_tuning(
    *,
    repo_root: str | None,
    assigned_client: "LLMClient",
    primary_client: "LLMClient",
    expert_client: "LLMClient | None" = None,
) -> PromptScope | None:
    return await ensure_role_tuning(
        repo_root=repo_root,
        agent_role=PRIMARY_ROLE,
        assigned_client=assigned_client,
        primary_client=primary_client,
        expert_client=expert_client,
    )


async def ensure_expert_role_tuning(
    *,
    repo_root: str | None,
    assigned_client: "LLMClient",
    primary_client: "LLMClient",
    expert_client: "LLMClient | None" = None,
) -> PromptScope | None:
    return await ensure_role_tuning(
        repo_root=repo_root,
        agent_role=EXPERT_ROLE,
        assigned_client=assigned_client,
        primary_client=primary_client,
        expert_client=expert_client,
    )
