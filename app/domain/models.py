from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, Field

MemoryType = Literal[
    "preference",
    "fact",
    "context",
    "task",
    "relationship",
    "instruction",
    "goal",
]

MemoryLifetime = Literal[
    "ephemeral",
    "short",
    "long",
]

class Decision(StrEnum):
    NO_REPLY = "no_reply"
    LATE_REPLY = "reply_scheduled"
    REPLY_NOW = "reply_now"

class MessageIn(BaseModel):
    conversation_id: str
    text: str = Field(min_length=1, max_length=8000)
    sender_id: str = "user"
    sender_name: str = "Unknown"

class OutboundMessage(BaseModel):
    conversation_id: str
    persona_id: str
    text: str
    scheduled_for: float | None = None
    event_type: str = "reply"
    metadata: dict[str, Any] = Field(default_factory=dict)

class Memory(BaseModel):
    content: str = Field(
        ...,
        description="The factual or contextual information worth remembering."
    )
    type: MemoryType
    lifetime: MemoryLifetime
    importance: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="How important this memory is for future interactions."
    )

class MemoryDecision(BaseModel):
    memories: list[Memory] = Field(
        default_factory=list,
        description="Memories extracted from the current interaction."
    )


class AgentResponse(BaseModel):
    response: str = Field(..., min_length=1)
    trust_factor: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Trust delta from this interaction between -1.0 and 1.0 (0 means unchanged, positive increases trust, negative decreases trust)."
    )
    memories: list[Memory] = Field(default_factory=list)


class ContactInfo(BaseModel):
    id: int | None = None
    persona_id: str
    contact_id: str
    name: str = "Unknown"
    trust: float = 0.0
    source: str | None = None
    is_unknown: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

