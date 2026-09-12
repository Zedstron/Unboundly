import asyncio

from app.agents.persona_graph import PersonaAgentGraph
from app.domain.models import AgentResponse


class FakeAI:
    async def classify_event(self, _text, _allowed_events):
        return "compliment"

    async def chat(self, messages, *, temperature):
        assert messages[0]["role"] == "system"
        assert temperature == 0.85
        return AgentResponse(response="that is sweet", memories=[])


class FakeShortMemory:
    async def get_short_memories(self, _key):
        return [{"content": "likes tea"}]


class FakeLongMemory:
    async def search(self, _key, _query):
        return [{"content": "has a dog"}]


def persona() -> dict:
    return {
        "id": "test",
        "profile": {"name": "Test", "language_style": ["en"]},
        "traits": {},
    }


async def _run_graph(monkeypatch):
    agent = PersonaAgentGraph(persona(), redis=None)
    agent.ai = FakeAI()
    agent.short_memory = FakeShortMemory()
    agent.long_memory = FakeLongMemory()

    async def fake_history(_persona_id, _conversation_id, limit):
        assert limit == 15
        return [{"direction": "user", "content": "you are cute"}]

    monkeypatch.setattr("app.agents.persona_graph.get_conversation", fake_history)

    event = await agent.classify_event("you are cute", ["compliment", "conflict"])
    reply = await agent.generate_reply("conversation", {"valence": 0.4})

    assert event == "compliment"
    assert reply == "that is sweet"


def test_persona_agent_graph_executes_classification_and_reply_paths(monkeypatch):
    asyncio.run(_run_graph(monkeypatch))
