from __future__ import annotations

import json

import pytest

from lean_ai.llm import role_tuning
from lean_ai.llm.role_tuning import (
    CandidateResult,
    DerivedPromptGuidance,
    RoleDiscoveryResult,
    RoleTuningProfile,
    choose_judge_client,
    ensure_request_role_scoped_overrides,
    ensure_request_role_tuning,
    load_role_tuning_profile,
    profile_is_current,
    request_prompt_scope,
    role_tuning_profile_path,
)
from lean_ai.workflow.prompts import build_request_system_prompt


class FakeClient:
    def __init__(self, provider_name: str, model_name: str, *, discovery=None, judge=None):
        self.provider_name = provider_name
        self.model_name = model_name
        self.discovery = discovery
        self.judge = judge
        self.raw_calls: list[list[dict]] = []
        self.structured_calls: list[tuple[list[dict], type]] = []

    async def chat_raw(self, messages, *args, **kwargs):
        self.raw_calls.append(messages)
        return "probe response"

    async def chat_structured(self, messages, schema, *args, **kwargs):
        self.structured_calls.append((messages, schema))
        if schema is RoleDiscoveryResult:
            return self.discovery
        return self.judge


def _sample_profile(assigned_model: str) -> RoleTuningProfile:
    return RoleTuningProfile(
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        agent_role="request",
        assigned_model=assigned_model,
        judge_model="ollama:judge",
        judge_role="expert",
        work_summary_hash=role_tuning.request_work_summary_hash(),
        prompt_version_hash=role_tuning.request_prompt_version_hash(),
        selected_role_title="Requirements Analyst",
        alternate_role_titles=["Business Analyst"],
        role_discovery=RoleDiscoveryResult(
            best_role_title="Requirements Analyst",
            alternate_role_titles=["Business Analyst"],
            why_this_role_fits="Best matches the work.",
            required_behaviors=["Ask focused questions."],
            behaviors_to_avoid=["Premature architecture choices."],
            risks_if_role_is_misunderstood=["Could drift into implementation."],
            plain_language_role_contract=["Stay focused on stakeholder outcomes."],
        ),
        candidate_results=[
            CandidateResult(
                candidate_role_title="Requirements Analyst",
                raw_total_score=46,
                consistency_penalty=0,
                final_score=46,
                pass_status="pass",
                recommended_role_contract=["Ask focused questions."],
                recommended_prompt_override_guidance="Avoid implementation details.",
            )
        ],
        approved_role_contract=["Ask focused questions."],
        derived_prompt_guidance=DerivedPromptGuidance(
            role_title="Requirements Analyst",
            required_behaviors=["Ask focused questions."],
            avoid_behaviors=["Premature architecture choices."],
            prompt_override_guidance="Avoid implementation details.",
        ),
    )


def test_choose_judge_client_prefers_expert_then_primary_then_self() -> None:
    assigned = FakeClient("ollama", "request-model")
    primary = FakeClient("ollama", "primary-model")
    expert = FakeClient("ollama", "expert-model")

    judge = choose_judge_client(
        assigned_client=assigned,
        primary_client=primary,
        expert_client=expert,
    )
    assert judge.client is expert
    assert judge.judge_role == "expert"
    assert judge.warning is None

    judge = choose_judge_client(
        assigned_client=assigned,
        primary_client=primary,
        expert_client=None,
    )
    assert judge.client is primary
    assert judge.judge_role == "primary"

    judge = choose_judge_client(
        assigned_client=assigned,
        primary_client=assigned,
        expert_client=None,
    )
    assert judge.client is assigned
    assert judge.warning is not None


def test_role_tuning_profile_path_is_stable(tmp_path) -> None:
    scope = request_prompt_scope(FakeClient("ollama", "qwen3.5:32b"))
    path = role_tuning_profile_path(str(tmp_path), scope)
    assert path.parent.name == "role_tuning"
    assert path.name.startswith("request--ollama-qwen3-5-32b")


def test_request_prompt_scoped_override_preserves_placeholders(tmp_path) -> None:
    scope = request_prompt_scope(FakeClient("ollama", "qwen3"))
    role_tuning.registry.load(str(tmp_path))
    profile = _sample_profile(scope.model_id)

    ensure_request_role_scoped_overrides(str(tmp_path), scope, profile)
    role_tuning.registry.load(str(tmp_path))

    request_prompt = build_request_system_prompt(
        "",
        repo_root=str(tmp_path),
        prompt_scope=scope,
    )
    chat_prompt = role_tuning.registry.format_text(
        "chat.system",
        scope=scope,
        CHAT_MAX_TURNS="9",
    )

    assert "MODEL-SPECIFIC REQUEST ROLE TUNING" in request_prompt
    assert "Ask focused questions." in request_prompt
    assert "Avoid implementation details." in request_prompt
    assert "9" in chat_prompt
    assert "{CHAT_MAX_TURNS}" not in chat_prompt


@pytest.mark.asyncio
async def test_ensure_request_role_tuning_persists_and_reuses_profile(tmp_path) -> None:
    role_tuning.registry.load(str(tmp_path))
    discovery = RoleDiscoveryResult(
        best_role_title="Requirements Analyst",
        alternate_role_titles=["Business Analyst"],
        why_this_role_fits="Best match.",
        required_behaviors=["Ask focused questions."],
        behaviors_to_avoid=["Premature architecture choices."],
        risks_if_role_is_misunderstood=["Could drift into implementation."],
        plain_language_role_contract=["Stay focused on stakeholder outcomes."],
    )
    judge = role_tuning.JudgeEvaluation(
        agent_role="request",
        candidate_role_title="Requirements Analyst",
        probe_scores=role_tuning.ProbeScoreSet(
            role_definition=role_tuning.ProbeScore(
                stakeholder_communication=2,
                requirements_gathering=2,
                question_discipline=2,
                avoiding_premature_implementation=2,
                user_outcome_focus=2,
                downstream_engineering_usefulness=2,
                uncertainty_handling=2,
                role_boundary_discipline=2,
            ),
            scenario_judgment=role_tuning.ProbeScore(
                stakeholder_communication=2,
                requirements_gathering=2,
                question_discipline=2,
                avoiding_premature_implementation=2,
                user_outcome_focus=2,
                downstream_engineering_usefulness=2,
                uncertainty_handling=2,
                role_boundary_discipline=2,
            ),
            boundary_discipline=role_tuning.ProbeScore(
                stakeholder_communication=2,
                requirements_gathering=2,
                question_discipline=2,
                avoiding_premature_implementation=2,
                user_outcome_focus=2,
                downstream_engineering_usefulness=2,
                uncertainty_handling=2,
                role_boundary_discipline=2,
            ),
        ),
        recommended_role_contract=["Ask focused questions."],
        recommended_prompt_override_guidance="Avoid implementation details.",
    )

    assigned = FakeClient("ollama", "request-model", discovery=discovery, judge=judge)
    primary = FakeClient("ollama", "primary-model")
    expert = FakeClient("ollama", "expert-model", judge=judge)

    scope = await ensure_request_role_tuning(
        repo_root=str(tmp_path),
        assigned_client=assigned,
        primary_client=primary,
        expert_client=expert,
    )

    assert scope is not None
    profile = load_role_tuning_profile(str(tmp_path), scope)
    assert profile is not None
    assert profile.selected_role_title == "Requirements Analyst"
    assert role_tuning.registry.get_scoped_override("chat.system", scope) is not None
    assert assigned.structured_calls, "expected discovery call"
    assert expert.structured_calls, "expected judge call"

    reuse_assigned = FakeClient("ollama", "request-model")
    reused_scope = await ensure_request_role_tuning(
        repo_root=str(tmp_path),
        assigned_client=reuse_assigned,
        primary_client=primary,
        expert_client=expert,
    )

    assert reused_scope == scope
    assert reuse_assigned.structured_calls == []
    assert reuse_assigned.raw_calls == []


@pytest.mark.asyncio
async def test_corrupt_profile_triggers_retune(tmp_path, monkeypatch) -> None:
    assigned = FakeClient("ollama", "request-model")
    scope = request_prompt_scope(assigned)
    profile_path = role_tuning_profile_path(str(tmp_path), scope)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("{not-json}\n", encoding="utf-8")

    called = False

    async def _fake_calibrate(**kwargs):
        nonlocal called
        called = True
        profile = _sample_profile(scope.model_id)
        role_tuning._persist_role_tuning_profile(str(tmp_path), scope, profile)
        ensure_request_role_scoped_overrides(str(tmp_path), scope, profile)
        return profile

    monkeypatch.setattr(role_tuning, "calibrate_request_role", _fake_calibrate)

    result_scope = await ensure_request_role_tuning(
        repo_root=str(tmp_path),
        assigned_client=assigned,
        primary_client=assigned,
        expert_client=None,
    )

    assert called is True
    assert result_scope == scope


def test_profile_is_current_tracks_hash_invalidations() -> None:
    scope = request_prompt_scope(FakeClient("ollama", "request-model"))
    profile = _sample_profile(scope.model_id)

    assert profile_is_current(profile, scope) is True

    stale = profile.model_copy(update={"work_summary_hash": "changed"})
    assert profile_is_current(stale, scope) is False

    stale = profile.model_copy(update={"prompt_version_hash": "changed"})
    assert profile_is_current(stale, scope) is False
