"""Regression tests for the chat/refinement prompt defaults."""

from lean_ai.llm.prompt_registry import registry


def test_chat_system_prompt_enforces_shared_understanding_loop():
    """The Grill Me prompt should keep drilling until ambiguity is resolved."""
    prompt = registry.get("chat.system")

    assert "shared understanding" in prompt
    assert "Do not stop early just because you have one plausible interpretation." in prompt
    assert "If the current answer leaves ambiguity on the same branch" in prompt
    assert "Current understanding:" in prompt
    assert "If a coding agent would still need to guess what the user means" in prompt


def test_refiner_chat_prompt_surfaces_ambiguity_instead_of_hiding_it():
    """Chat refinement should preserve open decisions for later grilling."""
    prompt = registry.get("refiner.chat")

    assert "Do NOT hide ambiguity by guessing or smoothing it over" in prompt
    assert "CONFIRMED FACTS:" in prompt
    assert "ASSUMPTIONS TO VERIFY:" in prompt
    assert "OPEN QUESTIONS:" in prompt


def test_refiner_task_prompt_surfaces_unresolved_decisions():
    """Task refinement should expose unresolved decisions to the planner."""
    prompt = registry.get("refiner.task")

    assert "Do NOT hide ambiguity by guessing or smoothing it over" in prompt
    assert "CONFIRMED FACTS:" in prompt
    assert "ASSUMPTIONS TO VERIFY:" in prompt
    assert "OPEN QUESTIONS:" in prompt
