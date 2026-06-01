from __future__ import annotations

import pytest

from lean_ai.llm import role_tuning
from lean_ai.llm.prompt_registry import PromptScope
from lean_ai.llm.role_tuning import (
    CandidateResult,
    DerivedPromptGuidance,
    JudgeEvaluation,
    ProbeScore,
    ProbeScoreSet,
    RoleDiscoveryResult,
    RoleTuningProfile,
    RuntimePromptSummary,
    choose_judge_client,
    ensure_expert_role_tuning,
    ensure_primary_role_tuning,
    ensure_request_role_scoped_overrides,
    ensure_request_role_tuning,
    load_role_tuning_profile,
    profile_is_current,
    prompt_scope_for_role,
    role_prompt_version_hash,
    role_tuning_profile_path,
    role_work_summary_hash,
)
from lean_ai.workflow.prompts import build_fix_system_prompt, build_request_system_prompt


class FakeClient:
    def __init__(self, provider_name: str, model_name: str, *, discovery=None, judge=None, summary=None):
        self.provider_name = provider_name
        self.model_name = model_name
        self.discovery = discovery
        self.judge = judge
        self.summary = summary
        self.raw_calls: list[list[dict]] = []
        self.structured_calls: list[tuple[list[dict], type]] = []

    async def chat_raw(self, messages, *args, **kwargs):
        self.raw_calls.append(messages)
        return "probe response"

    async def chat_structured(self, messages, schema, *args, **kwargs):
        self.structured_calls.append((messages, schema))
        if schema is RoleDiscoveryResult:
            return self.discovery
        if schema is RuntimePromptSummary:
            return self.summary
        return self.judge


def _sample_discovery(best_title: str) -> RoleDiscoveryResult:
    return RoleDiscoveryResult(
        best_role_title=best_title,
        alternate_role_titles=["Business Analyst"],
        why_this_role_fits="Best matches the work.",
        required_behaviors=["Ask focused questions."],
        behaviors_to_avoid=["Premature architecture choices."],
        risks_if_role_is_misunderstood=["Could drift into implementation."],
        plain_language_role_contract=["Stay focused on stakeholder outcomes."],
    )


def _sample_profile(agent_role: str, assigned_model: str) -> RoleTuningProfile:
    return RoleTuningProfile(
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        agent_role=agent_role,
        assigned_model=assigned_model,
        judge_model="ollama:judge",
        judge_role="expert",
        work_summary_hash=role_work_summary_hash(agent_role),
        prompt_version_hash=role_prompt_version_hash(agent_role),
        selected_role_title="Requirements Analyst",
        alternate_role_titles=["Business Analyst"],
        role_discovery=_sample_discovery("Requirements Analyst"),
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
        runtime_prompt_summary=RuntimePromptSummary(
            role_title="Requirements Analyst",
            required_behaviors=["Separate stakeholder intent from implementation preferences."],
            avoid_behaviors=["Do not skip the prompt's required exploration loop."],
            prompt_override_guidance="Keep the role framing compact and non-technical.",
        ),
    )


def _sample_judge(agent_role: str, title: str) -> JudgeEvaluation:
    score = ProbeScore(
        clarity_and_communication=2,
        task_alignment=2,
        context_stewardship=2,
        reasoning_quality=2,
        premature_action_avoidance=2,
        downstream_usefulness=2,
        uncertainty_handling=2,
        role_boundary_discipline=2,
    )
    return JudgeEvaluation(
        agent_role=agent_role,
        candidate_role_title=title,
        probe_scores=ProbeScoreSet(
            role_definition=score,
            scenario_judgment=score,
            boundary_discipline=score,
        ),
        recommended_role_contract=["Ask focused questions."],
        recommended_prompt_override_guidance="Avoid implementation details.",
    )


def _sample_runtime_summary(title: str) -> RuntimePromptSummary:
    return RuntimePromptSummary(
        role_title=title,
        required_behaviors=["Separate stakeholder intent from implementation preferences."],
        avoid_behaviors=["Do not skip the prompt's required exploration loop."],
        prompt_override_guidance="Keep the role framing compact and non-technical.",
    )


def test_choose_judge_client_prefers_expert_then_primary_then_self() -> None:
    assigned = FakeClient("ollama", "request-model")
    primary = FakeClient("ollama", "primary-model")
    expert = FakeClient("ollama", "expert-model")

    judge = choose_judge_client(
        agent_role="request",
        assigned_client=assigned,
        primary_client=primary,
        expert_client=expert,
    )
    assert judge.client is expert
    assert judge.judge_role == "expert"
    assert judge.warning is None

    judge = choose_judge_client(
        agent_role="request",
        assigned_client=assigned,
        primary_client=primary,
        expert_client=None,
    )
    assert judge.client is primary
    assert judge.judge_role == "primary"

    judge = choose_judge_client(
        agent_role="request",
        assigned_client=assigned,
        primary_client=assigned,
        expert_client=None,
    )
    assert judge.client is assigned
    assert judge.warning is not None


def test_choose_judge_client_expert_role_prefers_primary_before_self() -> None:
    expert = FakeClient("ollama", "expert-model")
    primary = FakeClient("ollama", "primary-model")

    judge = choose_judge_client(
        agent_role="expert",
        assigned_client=expert,
        primary_client=primary,
        expert_client=expert,
    )

    assert judge.client is primary
    assert judge.judge_role == "primary"


def test_role_tuning_profile_path_is_stable(tmp_path) -> None:
    scope = prompt_scope_for_role(FakeClient("ollama", "qwen3.5:32b"), "request")
    path = role_tuning_profile_path(str(tmp_path), scope)
    assert path.parent.name == "role_tuning"
    assert path.name.startswith("request--ollama-qwen3-5-32b")


def test_request_prompt_scoped_override_preserves_placeholders(tmp_path) -> None:
    scope = prompt_scope_for_role(FakeClient("ollama", "qwen3"), "request")
    role_tuning.registry.load(str(tmp_path))
    profile = _sample_profile(scope.agent_role, scope.model_id)

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
    assert "Separate stakeholder intent from implementation preferences." in request_prompt
    assert "Do not skip the prompt's required exploration loop." in request_prompt
    assert "Keep the role framing compact and non-technical." in request_prompt
    assert "9" in chat_prompt
    assert "{CHAT_MAX_TURNS}" not in chat_prompt


def test_primary_prompt_scoped_override_preserves_base_prompt(tmp_path) -> None:
    scope = PromptScope(model_id="ollama:qwen3", agent_role="primary")
    profile = RoleTuningProfile(
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        agent_role="primary",
        assigned_model=scope.model_id,
        judge_model="ollama:judge",
        judge_role="expert",
        work_summary_hash=role_work_summary_hash("primary"),
        prompt_version_hash=role_prompt_version_hash("primary"),
        selected_role_title="Senior Software Engineer",
        role_discovery=_sample_discovery("Senior Software Engineer"),
        approved_role_contract=["Verify signatures against the codebase."],
        derived_prompt_guidance=DerivedPromptGuidance(
            role_title="Senior Software Engineer",
            required_behaviors=["Verify signatures against the codebase."],
            avoid_behaviors=["Do not invent file contents."],
            prompt_override_guidance="Keep changes small and verified.",
        ),
        runtime_prompt_summary=RuntimePromptSummary(
            role_title="Senior Software Engineer",
            required_behaviors=["Verify repository facts before changing code."],
            avoid_behaviors=["Do not broaden the task into redesign work."],
            prompt_override_guidance="Keep changes small and verified.",
        ),
    )

    role_tuning.registry.load(str(tmp_path))
    role_tuning.ensure_role_scoped_overrides(str(tmp_path), scope, profile)
    role_tuning.registry.load(str(tmp_path))

    prompt = build_fix_system_prompt("", repo_root=str(tmp_path), prompt_scope=scope)

    assert "MODEL-SPECIFIC PRIMARY ROLE TUNING" in prompt
    assert "Verify repository facts before changing code." in prompt
    assert "call task_complete" in prompt


@pytest.mark.asyncio
async def test_ensure_request_role_tuning_persists_and_reuses_profile(tmp_path) -> None:
    role_tuning.registry.load(str(tmp_path))
    discovery = _sample_discovery("Requirements Analyst")
    judge = _sample_judge("request", "Requirements Analyst")
    summary = _sample_runtime_summary("Requirements Analyst")

    assigned = FakeClient("ollama", "request-model", discovery=discovery, judge=judge)
    primary = FakeClient("ollama", "primary-model", judge=judge, summary=summary)
    expert = FakeClient("ollama", "expert-model", judge=judge, summary=summary)

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
    assert profile.runtime_prompt_summary.required_behaviors == summary.required_behaviors
    assert role_tuning.registry.get_scoped_override("chat.system", scope) is not None
    assert assigned.structured_calls, "expected discovery call"
    assert any(schema is JudgeEvaluation for _, schema in expert.structured_calls), "expected judge call"
    assert any(schema is RuntimePromptSummary for _, schema in expert.structured_calls), "expected summary call"

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
@pytest.mark.parametrize(
    ("agent_role", "ensure_fn", "expected_prompt_key", "title"),
    [
        ("primary", ensure_primary_role_tuning, "planning.scope_system", "Senior Software Engineer"),
        ("expert", ensure_expert_role_tuning, "planning.design_system", "Software Architect"),
    ],
)
async def test_ensure_non_request_role_tuning_persists_scoped_overrides(
    tmp_path,
    agent_role,
    ensure_fn,
    expected_prompt_key,
    title,
) -> None:
    role_tuning.registry.load(str(tmp_path))
    discovery = _sample_discovery(title)
    judge = _sample_judge(agent_role, title)
    summary = _sample_runtime_summary(title)

    assigned = FakeClient("ollama", f"{agent_role}-model", discovery=discovery, judge=judge)
    primary = FakeClient("ollama", "primary-model", judge=judge, summary=summary)
    expert = FakeClient("ollama", "expert-model", judge=judge, summary=summary)

    scope = await ensure_fn(
        repo_root=str(tmp_path),
        assigned_client=assigned,
        primary_client=primary,
        expert_client=expert,
    )

    assert scope is not None
    assert scope.agent_role == agent_role
    profile = load_role_tuning_profile(str(tmp_path), scope)
    assert profile is not None
    assert profile.selected_role_title == title
    assert profile.runtime_prompt_summary.role_title == title
    assert role_tuning.registry.get_scoped_override(expected_prompt_key, scope) is not None


@pytest.mark.asyncio
async def test_corrupt_profile_triggers_retune(tmp_path, monkeypatch) -> None:
    assigned = FakeClient("ollama", "request-model")
    scope = prompt_scope_for_role(assigned, "request")
    profile_path = role_tuning_profile_path(str(tmp_path), scope)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("{not-json}\n", encoding="utf-8")

    called = False

    async def _fake_calibrate(**kwargs):
        nonlocal called
        called = True
        profile = _sample_profile(scope.agent_role, scope.model_id)
        role_tuning._persist_role_tuning_profile(str(tmp_path), scope, profile)
        role_tuning.ensure_role_scoped_overrides(str(tmp_path), scope, profile)
        return profile

    monkeypatch.setattr(role_tuning, "calibrate_role", _fake_calibrate)

    result_scope = await ensure_request_role_tuning(
        repo_root=str(tmp_path),
        assigned_client=assigned,
        primary_client=assigned,
        expert_client=None,
    )

    assert called is True
    assert result_scope == scope


def test_profile_is_current_tracks_hash_invalidations() -> None:
    scope = prompt_scope_for_role(FakeClient("ollama", "request-model"), "request")
    profile = _sample_profile(scope.agent_role, scope.model_id)

    assert profile_is_current(profile, scope) is True

    stale = profile.model_copy(update={"work_summary_hash": "changed"})
    assert profile_is_current(stale, scope) is False

    stale = profile.model_copy(update={"prompt_version_hash": "changed"})
    assert profile_is_current(stale, scope) is False


@pytest.mark.asyncio
async def test_request_runtime_summary_prompt_reviews_chat_and_request_surfaces(tmp_path) -> None:
    role_tuning.registry.load(str(tmp_path))
    discovery = _sample_discovery("Requirements Analyst")
    judge = _sample_judge("request", "Requirements Analyst")
    summary = _sample_runtime_summary("Requirements Analyst")

    assigned = FakeClient("ollama", "request-model", discovery=discovery, judge=judge)
    primary = FakeClient("ollama", "primary-model", judge=judge)
    expert = FakeClient("ollama", "expert-model", judge=judge, summary=summary)

    await ensure_request_role_tuning(
        repo_root=str(tmp_path),
        assigned_client=assigned,
        primary_client=primary,
        expert_client=expert,
    )

    summary_messages = next(messages for messages, schema in expert.structured_calls if schema is RuntimePromptSummary)
    prompt = summary_messages[-1]["content"]

    assert "Prompt key: chat.system" in prompt
    assert "Prompt key: fix.request_system" in prompt
    assert "one-question-at-a-time rule" in prompt
    assert "CHAT_MAX_TURNS" in prompt


def test_scoped_override_uses_runtime_summary_not_full_derived_guidance(tmp_path) -> None:
    scope = prompt_scope_for_role(FakeClient("ollama", "qwen3"), "request")
    role_tuning.registry.load(str(tmp_path))
    profile = _sample_profile(scope.agent_role, scope.model_id).model_copy(
        update={
            "approved_role_contract": [
                "Ask a small number of focused questions at a time.",
                "Capture business goals, user workflow, constraints, edge cases, and success criteria.",
                "Stay focused on stakeholder outcomes.",
            ],
            "derived_prompt_guidance": DerivedPromptGuidance(
                role_title="Requirements Analyst",
                required_behaviors=[
                    "Ask a small number of focused questions at a time.",
                    "Capture business goals, user workflow, constraints, edge cases, and success criteria.",
                    "Stay focused on stakeholder outcomes.",
                ],
                avoid_behaviors=[
                    "Premature architecture choices.",
                    "Could drift into implementation.",
                    "Do not jump directly into architecture or framework selection.",
                ],
                prompt_override_guidance="Longer legacy guidance that should not be injected anymore.",
            ),
            "runtime_prompt_summary": RuntimePromptSummary(
                role_title="Requirements Analyst",
                required_behaviors=["Ask one high-signal question at a time."],
                avoid_behaviors=["Do not bypass the required exploration loop."],
                prompt_override_guidance="Stay compatible with the Grill Me protocol.",
            ),
        }
    )

    role_tuning.ensure_role_scoped_overrides(str(tmp_path), scope, profile)
    role_tuning.registry.load(str(tmp_path))
    chat_prompt = role_tuning.registry.get_text("chat.system", scope=scope)

    assert "Ask one high-signal question at a time." in chat_prompt
    assert "Do not bypass the required exploration loop." in chat_prompt
    assert "Stay compatible with the Grill Me protocol." in chat_prompt
    assert "Longer legacy guidance that should not be injected anymore." not in chat_prompt


def test_runtime_summary_override_is_meaningfully_smaller_than_legacy_derived_guidance(tmp_path) -> None:
    scope = prompt_scope_for_role(FakeClient("ollama", "qwen3"), "request")
    role_tuning.registry.load(str(tmp_path))
    profile = _sample_profile(scope.agent_role, scope.model_id).model_copy(
        update={
            "derived_prompt_guidance": DerivedPromptGuidance(
                role_title="Requirements Analyst",
                required_behaviors=[
                    "Ask a small number of focused questions at a time.",
                    "Capture business goals, user workflow, constraints, edge cases, and success criteria.",
                    "Separate user intent from implementation preferences.",
                    "Produce a structured request document downstream agents can use.",
                    "Use plain language with non-technical stakeholders.",
                ],
                avoid_behaviors=[
                    "Premature architecture choices.",
                    "Could drift into implementation.",
                    "Do not jump directly into architecture or framework selection.",
                    "Do not invent business rules or hidden requirements.",
                    "Do not ask stakeholders to define schemas, databases, or internal implementation details.",
                ],
                prompt_override_guidance="Legacy guidance text repeated across multiple sections for runtime injection size comparison.",
            ),
            "runtime_prompt_summary": RuntimePromptSummary(
                role_title="Requirements Analyst",
                required_behaviors=["Separate stakeholder intent from implementation preferences."],
                avoid_behaviors=["Do not bypass the required exploration loop."],
                prompt_override_guidance="Stay compatible with the Grill Me protocol.",
            ),
        }
    )

    legacy_text = role_tuning._build_scoped_prompt_override_text(
        role_tuning._role_config(scope.agent_role),
        role_tuning.registry.get_text("chat.system"),
        profile.model_copy(
            update={
                "runtime_prompt_summary": RuntimePromptSummary(
                    role_title=profile.derived_prompt_guidance.role_title,
                    required_behaviors=profile.derived_prompt_guidance.required_behaviors,
                    avoid_behaviors=profile.derived_prompt_guidance.avoid_behaviors,
                    prompt_override_guidance=profile.derived_prompt_guidance.prompt_override_guidance,
                )
            }
        ),
    )
    compact_text = role_tuning._build_scoped_prompt_override_text(
        role_tuning._role_config(scope.agent_role),
        role_tuning.registry.get_text("chat.system"),
        profile,
    )

    assert len(compact_text) < len(legacy_text)
