"""Graph operation that runs only the memory agent over an inbound message."""
from typing import Any

from app.infrastructure.ai import AIProvider
from app.infrastructure.memory import LongTermMemory, ShortTermMemory
from app.infrastructure.sqlite import get_conversation, get_contact

from app.agents.nodes.memory_update import decide_memories_node, save_memories_node
from app.agents.state import PersonaGraphState


async def remember_message_node(
    state: PersonaGraphState,
    persona_id: str,
    short_memory: ShortTermMemory,
    long_memory: LongTermMemory,
    ai: AIProvider,
) -> dict[str, Any]:
    """Fetch context and persist memories for a message the persona never replied to.

    This is the reply pipeline minus message generation: memories and trust
    still update, but nothing is sent to anyone. Reuses the same memory agent
    nodes as the reply path so both paths behave identically.
    """
    conversation_id = state["conversation_id"]
    memory_key = persona_id + conversation_id

    history = await get_conversation(persona_id, conversation_id, limit=15)
    short_memories = await short_memory.get_short_memories(memory_key)

    long_memories: list[dict[str, Any]] = []
    if history:
        long_memories = await long_memory.search(memory_key, history[-1]["content"])

    contact_id = state.get("sender_id") or conversation_id
    contact = await get_contact(persona_id, contact_id)
    if contact is None and contact_id != conversation_id:
        contact = await get_contact(persona_id, conversation_id)

    context: PersonaGraphState = {
        "conversation_id": conversation_id,
        "sender_id": contact_id,
        "sender_name": state.get("sender_name"),
        "source": state.get("source"),
        "text": state.get("text"),
        "history": history,
        "short_memories": short_memories,
        "long_memories": long_memories,
        "contact": contact,
        "mood": state.get("mood") or {},
    }

    decision_result = await decide_memories_node(context, ai)
    context.update(decision_result)
    await save_memories_node(context, persona_id, short_memory, long_memory)

    return decision_result
