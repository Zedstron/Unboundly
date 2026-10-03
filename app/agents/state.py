from typing import Any, Literal, TypedDict
from app.domain.models import AgentResponse, MemoryDecision


class PersonaGraphState(TypedDict, total=False):
    operation: Literal["classify", "reply", "remember"]
    conversation_id: str
    sender_id: str | None
    sender_name: str | None
    source: str | None
    text: str
    reply_to_message_id: str | None
    reply_to_text: str | None
    initiative: str | None
    mood: dict[str, float]
    contact: dict[str, Any] | None
    allowed_events: list[str]
    event: str
    history: list[dict[str, str]]
    short_memories: list[dict[str, Any]]
    long_memories: list[dict[str, Any]]
    messages: list[dict[str, str]]
    agent_response: AgentResponse
    memory_decision: MemoryDecision
    reply: str
    trust_factor: float

