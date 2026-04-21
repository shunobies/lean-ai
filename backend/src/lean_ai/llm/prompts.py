"""All system prompts in one place.

No persona assignment — capability-first framing only.

This module re-exports prompt constants from the central PromptRegistry
for backward compatibility.  New code should use ``prompt_registry.registry``
directly.  Prompts that compose policy blocks are resolved at access time
so that user overrides take effect.
"""

from lean_ai.llm.prompt_registry import registry

# ── Canonical policy blocks (composed into mode prompts) ──────────


def _policy(key: str) -> str:
    return registry.get(key)


# These are accessed as module-level attributes.  We use __getattr__
# so the registry is consulted at access time (not import time),
# picking up any overrides loaded after import.

_SIMPLE_KEYS: dict[str, str] = {
    "TOOL_POLICY": "policy.tool",
    "COMPLETION_CONTRACT": "policy.completion",
    "QUALITY_RULES": "policy.quality",
    "WEB_SEARCH_POLICY": "policy.web_search",
    "SCRATCHPAD_POLICY": "policy.scratchpad",
    "SYSTEM_PROMPT": "execution.system",
    "PLAN_SCOPE_SYSTEM_PROMPT": "planning.scope_system",
    "FIX_INVESTIGATION_PROMPT": "fix.investigation",
    "CHAT_SYSTEM_PROMPT": "chat.system",
    "REFINER_CHAT_PROMPT": "refiner.chat",
    "REFINER_TASK_PROMPT": "refiner.task",
    "PRIVACY_STRIP_PROMPT": "refiner.privacy_strip",
}


class _MissingKey(dict):
    """format_map substitution map that leaves unknown placeholders
    in place so downstream ``.format()`` calls (e.g. per-phase user
    prompts with their own template vars) still see them."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _compose(key: str) -> str:
    """Resolve a prompt that embeds policy block placeholders."""
    from lean_ai.config import settings

    text = registry.get(key)
    web_search = registry.get("policy.web_search")
    if settings.wiki_url:
        web_search += "\n" + registry.get("policy.wiki_search")
    if settings.enable_claim_verification:
        web_search += "\n" + registry.get("policy.claim_verification")
    if settings.enable_required_citations:
        web_search += "\n" + registry.get("policy.required_citations")

    # Phase 5 strict-test-contract policy block. Empty string when the
    # flag is off so previous-turn prompt content is preserved. The
    # testing-environment awareness block is composed in-line at the
    # end so Phase 5 sees setup-first guidance together with the rest
    # of the strict contract.
    strict_test_contract = ""
    if settings.enable_strict_test_contract:
        strict_test_contract = registry.get("policy.strict_test_contract")
        strict_test_contract += "\n\n" + registry.get(
            "policy.testing_environment_awareness",
        )

    # Phase 4 testability requirement. Gated by the same strict flag.
    testability_requirement = ""
    if settings.enable_strict_test_contract:
        testability_requirement = registry.get(
            "policy.testability_requirement",
        )

    # Standalone testing-environment awareness for Phase 2's
    # exploration system prompt (where the strict contract block
    # would be off-topic but the detection guidance still applies).
    testing_environment_awareness = ""
    if settings.enable_strict_test_contract:
        testing_environment_awareness = registry.get(
            "policy.testing_environment_awareness",
        )

    subs = _MissingKey({
        "TOOL_POLICY": registry.get("policy.tool"),
        "COMPLETION_CONTRACT": registry.get("policy.completion"),
        "QUALITY_RULES": registry.get("policy.quality"),
        "WEB_SEARCH_POLICY": web_search,
        "SCRATCHPAD_POLICY": registry.get("policy.scratchpad"),
        "STRICT_TEST_CONTRACT": strict_test_contract,
        "TESTABILITY_REQUIREMENT": testability_requirement,
        "TESTING_ENVIRONMENT_AWARENESS": testing_environment_awareness,
    })
    try:
        return text.format_map(subs)
    except KeyError:
        return text


_COMPOSED_KEYS: dict[str, str] = {
    "IMPLEMENTATION_SYSTEM_PROMPT": "execution.implementation_system",
    "STEP_EXECUTION_SYSTEM_PROMPT": "execution.step_system",
    "FIX_SYSTEM_PROMPT": "fix.system",
    "REQUEST_SYSTEM_PROMPT": "fix.request_system",
    "PLAN_VERIFICATION_SYSTEM_PROMPT": "planning.verification_system",
    "PLAN_DESIGN_SYSTEM_PROMPT": "planning.design_system",
    "PLAN_ASSEMBLY_SYSTEM_PROMPT": "planning.assembly_system",
    "PLAN_EXPLORATION_SYSTEM_PROMPT": "planning.exploration_system",
}


def __getattr__(name: str) -> str:
    if name in _SIMPLE_KEYS:
        return registry.get(_SIMPLE_KEYS[name])
    if name in _COMPOSED_KEYS:
        return _compose(_COMPOSED_KEYS[name])
    if name == "PLAN_SYSTEM_PROMPT":
        return registry.get("planning.scope_system")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
