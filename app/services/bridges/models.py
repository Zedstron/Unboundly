from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from enum import StrEnum

class Operation(StrEnum):
    SEND_MESSAGE = "send_message"
    MARK_SEEN = "mark_seen"
    SET_ONLINE = "set_online"
    SEND_ATTACHMENT = "send_attachment"
    SEND_REACTION = "send_reaction"
    STOP_SERVICE = "stop_service"

@dataclass(slots=True)
class SocialMessage:
    session: str

    message_id: str
    message_type: str
    chat_id: str
    item_id: str

    sender_id: str
    sender_name: str | None

    text: str

    provider: str = "local"
    is_disappearing: bool = False
    timestamp: datetime | None = None

    raw: dict[str, Any] = field(default_factory=dict)