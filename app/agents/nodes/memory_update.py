from app.infrastructure.memory import LongTermMemory, ShortTermMemory

from app.agents.state import PersonaGraphState


async def save_memories_node(state: PersonaGraphState, persona_id: str, short_memory: ShortTermMemory, long_memory: LongTermMemory) -> dict:
    memory_key = persona_id + state["conversation_id"]

    for memory in state["agent_response"].memories:
        payload = memory.model_dump()

        if memory.lifetime == "short":
            await short_memory.save_short_memory(memory_key, payload)

        elif memory.lifetime == "long":
            await long_memory.save(memory_key, payload)

    return {}
