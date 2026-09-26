from typing import Any

from app.infrastructure.memory import LongTermMemory, ShortTermMemory
from app.infrastructure.sqlite import get_conversation, get_contact

from app.agents.state import PersonaGraphState


async def retrieve_context_node(state: PersonaGraphState, persona_id: str, short_memory: ShortTermMemory, long_memory: LongTermMemory) -> dict[str, Any]:
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

    return {
        "history": history,
        "short_memories": short_memories,
        "long_memories": long_memories,
        "contact": contact
    }

