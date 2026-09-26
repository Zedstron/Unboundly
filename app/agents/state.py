from typing import Any, Literal, TypedDict
from app.domain.models import AgentResponse


class PersonaGraphState(TypedDict, total=False):
    operation: Literal["classify", "reply"]
    conversation_id: str
    sender_id: str | None
    sender_name: str | None
    source: str | None
    text: str
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
    reply: str
    trust_factor: float

