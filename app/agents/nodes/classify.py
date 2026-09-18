from collections.abc import Sequence

from app.core.logger import get_logger
from app.infrastructure.ai import AIProvider

from app.agents.state import PersonaGraphState

logger = get_logger(__name__)


async def classify_event_node(state: PersonaGraphState, ai: AIProvider) -> dict[str, str]:
    text = state["text"]
    allowed_events = state["allowed_events"]

    if not text or not text.strip():
        return {"event": "long_idle_gap"}

    try:
        event = await ai.classify_event(text, allowed_events)
    except Exception as exc:
        logger.warning("Event classification failed; using local fallback: %s", exc)
        event = fallback_classify_event(text, allowed_events)

    return {"event": event}


def fallback_classify_event(text: str, allowed_events: Sequence[str]) -> str:
    low = text.lower()
    candidates = (
        ("reassurance", ("sorry", "love you", "miss you", "reassure")),
        ("conflict", ("stupid", "hate you", "shut up", "angry")),
        ("compliment", ("beautiful", "cute", "good girl", "handsome")),
    )

    for event, keywords in candidates:
        if event in allowed_events and any(keyword in low for keyword in keywords):
            return event

    if "long_idle_gap" in allowed_events and len(low) < 3:
        return "long_idle_gap"

    return allowed_events[0]
