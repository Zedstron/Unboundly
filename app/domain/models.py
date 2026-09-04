from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field


class Decision(StrEnum):
    NO_REPLY = "no_reply"
    LATE_REPLY = "reply_scheduled"
    REPLY_NOW = "reply_now"

class MessageIn(BaseModel):
    conversation_id: str
    text: str = Field(min_length=1, max_length=8000)
    sender_id: str = "user"

class OutboundMessage(BaseModel):
    conversation_id: str
    persona_id: str
    text: str
    scheduled_for: float | None = None
    event_type: str = "reply"
    metadata: dict[str, Any] = Field(default_factory=dict)
