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
    "PLAN_EXPLORATION_SYSTEM_PROMPT": "planning.exploration_system",
    "PLAN_DESIGN_SYSTEM_PROMPT": "planning.design_system",
    "PLAN_VERIFICATION_SYSTEM_PROMPT": "planning.verification_system",
    "PLAN_ASSEMBLY_SYSTEM_PROMPT": "planning.assembly_system",
    "FIX_INVESTIGATION_PROMPT": "fix.investigation",
    "CHAT_SYSTEM_PROMPT": "chat.system",
    "REFINER_CHAT_PROMPT": "refiner.chat",
    "REFINER_TASK_PROMPT": "refiner.task",
    "PRIVACY_STRIP_PROMPT": "refiner.privacy_strip",
}


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
    subs = {
        "TOOL_POLICY": registry.get("policy.tool"),
        "COMPLETION_CONTRACT": registry.get("policy.completion"),
        "QUALITY_RULES": registry.get("policy.quality"),
        "WEB_SEARCH_POLICY": web_search,
        "SCRATCHPAD_POLICY": registry.get("policy.scratchpad"),
    }
    try:
        return text.format_map(subs)
    except KeyError:
        return text


_COMPOSED_KEYS: dict[str, str] = {
    "IMPLEMENTATION_SYSTEM_PROMPT": "execution.implementation_system",
    "STEP_EXECUTION_SYSTEM_PROMPT": "execution.step_system",
    "FIX_SYSTEM_PROMPT": "fix.system",
    "REQUEST_SYSTEM_PROMPT": "fix.request_system",
}


def __getattr__(name: str) -> str:
    if name in _SIMPLE_KEYS:
        return registry.get(_SIMPLE_KEYS[name])
    if name in _COMPOSED_KEYS:
        return _compose(_COMPOSED_KEYS[name])
    if name == "PLAN_SYSTEM_PROMPT":
        return registry.get("planning.scope_system")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
