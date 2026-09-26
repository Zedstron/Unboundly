from app.infrastructure.memory import LongTermMemory, ShortTermMemory
from app.infrastructure.sqlite import update_contact_trust

from app.agents.state import PersonaGraphState


async def save_memories_node(state: PersonaGraphState, persona_id: str, short_memory: ShortTermMemory, long_memory: LongTermMemory) -> dict:
    conversation_id = state["conversation_id"]
    memory_key = persona_id + conversation_id
    agent_response = state["agent_response"]

    for memory in agent_response.memories:
        payload = memory.model_dump()

        if memory.lifetime == "short":
            await short_memory.save_short_memory(memory_key, payload)

        elif memory.lifetime == "long":
            await long_memory.save(memory_key, payload)

    trust_factor = getattr(agent_response, "trust_factor", 0.0) or 0.0
    contact_id = state.get("sender_id") or conversation_id

    # If contact exists or if trust factor changed or we have contact info
    if contact_id:
        name = state.get("sender_name")
        source = state.get("source")
        await update_contact_trust(
            persona_id=persona_id,
            contact_id=contact_id,
            delta=trust_factor,
            name=name,
            source=source,
        )

    return { "trust_factor": trust_factor }

