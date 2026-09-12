import random
from datetime import datetime, timezone

from app.domain.behavior import BehaviorContext, BehaviorEngine
from app.domain.models import Decision
from app.domain.mood import MoodState


def persona() -> dict:
    return {
        "availability": {
            "online_probability_by_hour": [0.5] * 24,
            "busy_probability_by_hour": [0.2] * 24,
            "reply_probability_when_seen": 0.8,
        },
        "mood": {},
    }


def mood() -> MoodState:
    return MoodState(
        values={"arousal": 0.0, "fear": 0.0, "affection": 0.0, "curiosity": 0.0},
        updated_at=datetime.now(timezone.utc),
    )


def test_offline_presence_cannot_produce_a_reply():
    engine = BehaviorEngine(persona(), rng=random.Random(0))

    decision = engine.decide(mood(), BehaviorContext(12, 5, unread=True, online=False))

    assert decision is Decision.NO_REPLY


def test_busy_unread_message_is_delayed_not_immediately_replied_to():
    engine = BehaviorEngine(persona(), rng=random.Random(0))

    decision = engine.decide(mood(), BehaviorContext(12, 5, unread=True, online=True, busy=True))

    assert decision is Decision.LATE_REPLY


def test_busy_probability_is_configured_per_hour():
    config = persona()
    config["availability"]["busy_probability_by_hour"] = [0.0] * 24
    config["availability"]["busy_probability_by_hour"][9] = 0.75
    engine = BehaviorEngine(config)

    assert engine.busy_probability(9, mood()) == 0.75
