from typing import Any

from app.core.logger import get_logger
from app.core.prompts import get_prompt
from app.domain.models import MemoryDecision
from app.infrastructure.ai import AIProvider
from app.infrastructure.memory import LongTermMemory, ShortTermMemory
from app.infrastructure.sqlite import update_contact_trust

from app.agents.state import PersonaGraphState

logger = get_logger(__name__)


def _latest_user_text(state: PersonaGraphState) -> str:
    """The user message being replied to: the explicit text if present,
    otherwise the most recent user-direction message in history."""
    if text := state.get("text"):
        return text

    history = state.get("history") or []
    for message in reversed(history):
        if message.get("direction") == "user":
            return message.get("content", "")

    return ""


def _format_recent_memories(
    short_memories: list[dict[str, Any]],
    long_memories: list[dict[str, Any]],
) -> str:
    lines: list[str] = []

    for memory in long_memories:
        lines.append(f"- (long) {memory.get('content', '')}")

    for memory in short_memories:
        lines.append(f"- (recent) {memory.get('content', '')}")

    return "\n".join(lines) if lines else "(none yet)"


async def decide_memories_node(
    state: PersonaGraphState,
    ai: AIProvider,
) -> dict[str, MemoryDecision]:
    """Memory agent: reads only the latest exchange + recently recalled memories
    and decides what to store plus the trust delta. Runs independently of (and
    concurrently with) the message agent."""
    if state.get("initiative"):
        # Persona-initiated message: nothing new from the user this turn.
        user_text = "(none — the persona initiated this message)"
        override = (
            "\n\n[EXCHANGE OVERRIDE]\nThe persona initiated this message. There is no new "
            "user message to judge; return no memories and trust_factor 0.0 unless the "
            "persona's own message reveals something durable worth storing."
        )
    else:
        user_text = _latest_user_text(state)
        override = ""

    system = get_prompt("memory_decision", {
        "recent_memories": _format_recent_memories(
            state.get("short_memories", []),
            state.get("long_memories", []),
        ),
        "user_text": user_text,
        "persona_reply": (state.get("agent_response").text if state.get("agent_response") else ""),
    }) + override

    messages = [
        { "role": "system", "content": system },
        { "role": "user", "content": user_text },
    ]

    try:
        decision = await ai.decide_memories(messages, temperature=0.0)
    except Exception as exc:
        logger.warning("Memory decision failed; storing nothing and keeping trust unchanged: %s", exc)
        decision = MemoryDecision(memories=[], trust_factor=0.0)

    return { "memory_decision": decision }


async def save_memories_node(
    state: PersonaGraphState,
    persona_id: str,
    short_memory: ShortTermMemory,
    long_memory: LongTermMemory,
) -> dict:
    """Persist the memory agent's decision: store memories and apply the trust delta."""
    conversation_id = state["conversation_id"]
    memory_key = persona_id + conversation_id
    decision = state.get("memory_decision") or MemoryDecision()

    for memory in decision.memories:
        payload = memory.model_dump()

        if memory.lifetime == "short":
            await short_memory.save_short_memory(memory_key, payload)

        elif memory.lifetime == "long":
            await long_memory.save(memory_key, payload)

    trust_factor = decision.trust_factor or 0.0
    contact_id = state.get("sender_id") or conversation_id

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
