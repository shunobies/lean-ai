"""Regression tests for the architecture review chat prompt."""

from lean_ai.llm.prompt_registry import registry


def test_architecture_review_prompt_uses_deepening_vocabulary():
    prompt = registry.get("chat.architecture_review")

    assert "module, interface, implementation, depth, seam, adapter, leverage, locality" in prompt
    assert "Apply the deletion test" in prompt
    assert "Which of these would you like to explore?" in prompt


def test_architecture_review_prompt_requires_confirmation_before_recording():
    prompt = registry.get("chat.architecture_review")

    assert "Before using any decision-recording tool" in prompt
    assert "Never record a durable decision without user confirmation." in prompt
