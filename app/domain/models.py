from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

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

# Outbound message kinds the persona may produce.
# ``voice`` and ``image`` are reserved for future implementation: they stay in
# the schema so the model can express intent, but the pipeline refuses to send
# them until a provider implementation exists.
MessageType = Literal[
    "text",
    "reply",
    "voice",
    "reaction",
    "image",
]

UNIMPLEMENTED_MESSAGE_TYPES: frozenset[str] = frozenset({"voice", "image"})

class Decision(StrEnum):
    NO_REPLY = "no_reply"
    LATE_REPLY = "reply_scheduled"
    REPLY_NOW = "reply_now"

class MessageIn(BaseModel):
    conversation_id: str
    text: str = Field(min_length=1, max_length=8000)
    sender_id: str = "user"
    sender_name: str = "Unknown"
    reply_to_message_id: str | None = None
    reply_to_text: str | None = None

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
    """Memory agent output: what to store from this interaction and the trust delta."""

    memories: list[Memory] = Field(
        default_factory=list,
        description="Memories extracted from the current interaction."
    )
    trust_factor: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Trust delta from this interaction between -1.0 and 1.0 (0 means unchanged, positive increases trust, negative decreases trust)."
    )


class AgentResponse(BaseModel):
    """Message agent output: what to send back, and how.

    The persona answers with a JSON envelope so it can decide per message
    whether to send plain text, quote a specific earlier message, or react
    with a single emoji instead of replying.
    """

    type: MessageType = Field(
        default="text",
        description="How to deliver the message: text, reply (quoted), or reaction.",
    )
    text: str | None = Field(
        default=None,
        description="Message text. Required for type=text and type=reply; unused for reaction.",
    )
    reaction: str | None = Field(
        default=None,
        description="A single emoji. Required for type=reaction; unused otherwise.",
    )

    @model_validator(mode="after")
    def _validate_payload(self) -> "AgentResponse":
        if self.type in ("text", "reply"):
            text = (self.text or "").strip()
            if not text:
                raise ValueError(f"type={self.type} requires non-empty text")
            self.text = text
            self.reaction = None
        elif self.type == "reaction":
            reaction = (self.reaction or "").strip()
            if not reaction:
                raise ValueError("type=reaction requires a non-empty reaction")
            self.reaction = reaction
            self.text = None
        elif self.type in UNIMPLEMENTED_MESSAGE_TYPES:
            raise ValueError(f"type={self.type} is not implemented yet")
        return self


class ContactInfo(BaseModel):
    id: int | None = None
    persona_id: str
    contact_id: str
    name: str = "Unknown"
    trust: float = 0.0
    relationship: str = "stranger"
    source: str | None = None
    is_unknown: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

