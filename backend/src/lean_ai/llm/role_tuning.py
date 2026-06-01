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

ROLE_TUNING_SCHEMA_VERSION = 1
ROLE_TUNING_COMPOSITION_VERSION = "role-tuning-v2"

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
        ]
    )
    return _sha256_text(
        ROLE_TUNING_COMPOSITION_VERSION,
        agent_role,
        tuned_prompt_text,
        tuning_prompts,
    )


def request_prompt_version_hash(repo_root: str | None = None) -> str:
    return role_prompt_version_hash(REQUEST_ROLE, repo_root)


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
    guidance = profile.derived_prompt_guidance
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
    if profile is not None and profile_is_current(profile, scope, repo_root=repo_root):
        ensure_role_scoped_overrides(repo_root, scope, profile)
        return scope

    try:
        await calibrate_role(
            repo_root=repo_root,
            agent_role=agent_role,
            assigned_client=assigned_client,
            primary_client=primary_client,
            expert_client=expert_client,
        )
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
