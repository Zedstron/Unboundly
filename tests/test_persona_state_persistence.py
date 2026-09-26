import asyncio
import pytest
from datetime import datetime, timezone
from app.domain.mood import MoodEngine, MoodState
from app.infrastructure.sqlite import (
    init_db,
    save_persona_state,
    get_persona_state,
    reset_persona_state,
)
from app.services.conversation import ConversationService


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.scheduled = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, key):
        return int(self.data.pop(key, None) is not None)

    async def publish(self, channel, message):
        pass


def dummy_persona():
    return {
        "id": "test_persona",
        "profile": {
            "name": "Test Persona",
            "gender": "female",
            "timezone": "UTC",
        },
        "mood": {
            "dimensions": [
                "valence",
                "arousal",
                "irritability",
                "affection",
            ],
            "baseline": {
                "valence": 0.5,
                "arousal": 0.2,
                "irritability": 0.1,
                "affection": 0.7,
            },
            "decay_per_hour": 0.05,
            "event_weights": {
                "conflict": {
                    "valence": -0.4,
                    "irritability": 0.6,
                    "arousal": 0.5,
                },
                "compliment": {
                    "valence": 0.3,
                    "affection": 0.2,
                },
            },
        },
        "availability": {
            "online_probability_by_hour": [0.5] * 24,
            "busy_probability_by_hour": [0.2] * 24,
        },
    }


@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()
    await reset_persona_state("test_persona")
    yield
    await reset_persona_state("test_persona")


@pytest.mark.asyncio
async def test_sqlite_save_and_get_persona_state():
    mood = {"valence": -0.3, "irritability": 0.8, "arousal": 0.6, "affection": 0.1}
    now = datetime.now(timezone.utc)

    await save_persona_state("test_persona", mood, now)

    loaded = await get_persona_state("test_persona")
    assert loaded is not None
    assert loaded["persona_id"] == "test_persona"
    assert loaded["mood"] == mood
    assert loaded["updated_at"].tzinfo is not None

    # Update state
    updated_mood = {"valence": 0.2, "irritability": 0.3, "arousal": 0.4, "affection": 0.5}
    await save_persona_state("test_persona", updated_mood)

    loaded_again = await get_persona_state("test_persona")
    assert loaded_again["mood"] == updated_mood

    # Reset state
    deleted = await reset_persona_state("test_persona")
    assert deleted is True
    assert await get_persona_state("test_persona") is None


@pytest.mark.asyncio
async def test_persona_mood_resumes_across_restarts():
    persona_cfg = dummy_persona()
    redis = FakeRedis()

    # 1. Initial process: Service starts with baseline mood
    service_1 = ConversationService(persona_cfg, redis)
    initial_state = await service_1.load_state()
    assert initial_state.values["irritability"] == 0.1
    assert initial_state.values["valence"] == 0.5

    # 2. Persona encounters conflict and becomes angry
    state_after_conflict = service_1.mood.update(initial_state, "conflict")
    await service_1._save_state(state_after_conflict)
    assert state_after_conflict.values["irritability"] == pytest.approx(0.7)
    assert state_after_conflict.values["valence"] == pytest.approx(0.1)

    # Verify state was saved to SQLite
    in_db = await get_persona_state("test_persona")
    assert in_db is not None
    assert in_db["mood"]["irritability"] == pytest.approx(0.7)

    # 3. Simulate process termination and restart:
    # Service instance is discarded. Fresh service instance is created.
    # We clear Redis to simulate cold restart or Redis restart.
    redis.data.clear()

    service_restarted = ConversationService(persona_cfg, redis)
    resumed_state = await service_restarted.load_state()

    # The persona MUST resume its angry state rather than resetting to baseline 0.1!
    assert resumed_state.values["irritability"] == pytest.approx(0.7)
    assert resumed_state.values["valence"] == pytest.approx(0.1)
    assert resumed_state.updated_at.tzinfo is not None



@pytest.mark.asyncio
async def test_persona_mood_reset_returns_to_baseline():
    persona_cfg = dummy_persona()
    redis = FakeRedis()

    # 1. Start service and set angry mood
    service = ConversationService(persona_cfg, redis)
    await service.load_state()
    await service.set_mood({"irritability": 0.9, "valence": -0.8})

    current = await service.get_state()
    assert current["mood"]["irritability"] == 0.9

    # 2. Reset the persona state
    reset_result = await service.reset_state()
    assert reset_result.values["irritability"] == 0.1  # baseline
    assert reset_result.values["valence"] == 0.5       # baseline

    # Verify SQLite row was deleted
    in_db = await get_persona_state("test_persona")
    assert in_db is None

    # 3. Simulate another process restart after reset
    service_after_reset = ConversationService(persona_cfg, redis)
    fresh_state = await service_after_reset.load_state()
    assert fresh_state.values["irritability"] == 0.1  # remains at baseline
    assert fresh_state.values["valence"] == 0.5


@pytest.mark.asyncio
async def test_manual_sqlite_deletion_resets_on_restart():
    persona_cfg = dummy_persona()
    redis = FakeRedis()

    service = ConversationService(persona_cfg, redis)
    await service.load_state()
    await service.set_mood({"irritability": 0.85})

    # User or DBA directly resets the state in SQLite (e.g. DELETE FROM persona_states)
    await reset_persona_state("test_persona")
    redis.data.clear()

    # On process restart, persona safely falls back to baseline
    restarted_service = ConversationService(persona_cfg, redis)
    state = await restarted_service.load_state()
    assert state.values["irritability"] == 0.1
