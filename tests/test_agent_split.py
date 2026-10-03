import asyncio

import pytest

from app.agents.nodes.memory_update import (
    _format_recent_memories,
    _latest_user_text,
    decide_memories_node,
)
from app.agents.state import PersonaGraphState
from app.domain.models import AgentResponse, Memory, MemoryDecision


class FlakyAI:
    """Simulates the memory LLM call failing."""

    async def decide_memories(self, messages, *, temperature):
        raise RuntimeError("llm down")


class StubAI:
    """Returns a canned decision and records the prompt it received."""

    def __init__(self, decision: MemoryDecision):
        self.decision = decision
        self.calls: list[list[dict]] = []

    async def decide_memories(self, messages, *, temperature):
        self.calls.append(list(messages))
        return self.decision


def _state(**overrides) -> PersonaGraphState:
    base = {
        "conversation_id": "conv1",
        "sender_id": "user1",
        "sender_name": "Alice",
        "source": "local",
        "text": "I just got a new job at the hospital",
        "history": [
            {"direction": "user", "content": "hey"},
            {"direction": "bot", "content": "hello!"},
            {"direction": "user", "content": "I just got a new job at the hospital"},
        ],
        "short_memories": [{"content": "likes tea"}],
        "long_memories": [{"content": "has a dog"}],
        "agent_response": AgentResponse(type="text", text="congrats!"),
    }
    base.update(overrides)
    return base


def test_latest_user_text_prefers_explicit_text_then_history():
    assert _latest_user_text(_state()) == "I just got a new job at the hospital"
    assert (
        _latest_user_text(_state(text=None)) == "I just got a new job at the hospital"
    )
    assert _latest_user_text(_state(text=None, history=[])) == ""


def test_format_recent_memories_combines_long_and_short():
    block = _format_recent_memories(
        [{"content": "likes tea"}], [{"content": "has a dog"}]
    )

    assert "- (long) has a dog" in block
    assert "- (recent) likes tea" in block


async def test_decide_memories_node_returns_llm_decision():
    decision = MemoryDecision(
        memories=[
            Memory(
                content="works at the hospital",
                type="fact",
                lifetime="long",
                importance=0.7,
            )
        ],
        trust_factor=0.05,
    )
    ai = StubAI(decision)

    result = await decide_memories_node(_state(), ai)

    assert result["memory_decision"] is decision
    # The prompt should include the exchange and prior memories.
    system = ai.calls[0][0]["content"]
    assert "works at the hospital" in system or "hospital" in system
    assert "has a dog" in system
    assert "congrats!" in system


async def test_decide_memories_node_survives_llm_failure():
    result = await decide_memories_node(_state(), FlakyAI())

    assert result["memory_decision"].memories == []
    assert result["memory_decision"].trust_factor == 0.0


async def test_decide_memories_node_initiative_overrides_exchange():
    ai = StubAI(MemoryDecision(memories=[], trust_factor=0.0))

    await decide_memories_node(_state(initiative="check_in"), ai)

    system = ai.calls[0][0]["content"]
    assert "EXCHANGE OVERRIDE" in system


def test_agent_response_is_reply_only():
    # The message agent output must not carry memories or trust anymore.
    response = AgentResponse(type="text", text="hi")
    assert response.model_dump() == {"type": "text", "text": "hi", "reaction": None}

    with pytest.raises(Exception):
        AgentResponse(type="text", text="")  # empty replies are invalid


def test_memory_decision_clamps_trust_factor():
    with pytest.raises(Exception):
        MemoryDecision(trust_factor=5.0)
