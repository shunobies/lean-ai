"""Tests for the ``ChatRequest.extended_turns`` budget override.

The chat endpoint defaults to 20 tool-calling turns. Callers that need
more (e.g. the ``/mock-interview`` extension command, which scores
multiple rounds of Q&A) can pass ``extended_turns`` to raise the
ceiling, capped at ``_CHAT_EXTENDED_TURNS_MAX`` so an external caller
can't request arbitrary compute.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def resolver():
    from lean_ai.routers.chat import _resolve_max_turns

    return _resolve_max_turns


@pytest.fixture
def constants():
    from lean_ai.routers import chat as chat_mod

    return chat_mod._CHAT_MAX_TURNS, chat_mod._CHAT_EXTENDED_TURNS_MAX


def test_default_when_none(resolver, constants) -> None:
    default, _ = constants
    assert resolver(None) == default


def test_below_default_clamps_up_to_default(resolver, constants) -> None:
    default, _ = constants
    assert resolver(5) == default
    assert resolver(default) == default


def test_between_default_and_max_uses_requested_value(resolver, constants) -> None:
    default, maximum = constants
    mid = (default + maximum) // 2
    assert resolver(mid) == mid


def test_above_max_clamps_down_to_max(resolver, constants) -> None:
    _, maximum = constants
    assert resolver(maximum) == maximum
    assert resolver(maximum + 10) == maximum
    assert resolver(10_000) == maximum


def test_zero_and_negative_treated_as_default(resolver, constants) -> None:
    default, _ = constants
    assert resolver(0) == default
    assert resolver(-5) == default


def test_chat_request_accepts_extended_turns_field() -> None:
    """Pydantic serialisation: the new optional field round-trips."""
    from lean_ai.routers.models import ChatRequest

    req = ChatRequest(message="hi", extended_turns=30)
    assert req.extended_turns == 30

    # Default is None when not provided.
    default_req = ChatRequest(message="hi")
    assert default_req.extended_turns is None
