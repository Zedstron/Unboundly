import asyncio
import pytest
from app.core.config import settings
from app.infrastructure.sqlite import (
    init_db,
    save_or_update_contact,
    get_contact,
    update_contact_trust,
    list_contacts,
    delete_contact,
)
from app.domain.models import AgentResponse
from app.services.conversation import ConversationService
from app.services.bridges.models import SocialMessage


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.published = []

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
        self.published.append((channel, message))

    async def rpush(self, key, val):
        pass

    async def ltrim(self, key, start, end):
        pass

    async def expire(self, key, ttl):
        pass

    async def zadd(self, key, mapping):
        pass


def dummy_persona():
    return {
        "id": "trust_test_persona",
        "profile": {
            "name": "Trust Tester",
            "gender": "female",
            "timezone": "UTC",
        },
        "mood": {
            "dimensions": ["valence", "arousal", "irritability", "affection"],
            "baseline": {"valence": 0.5, "arousal": 0.2, "irritability": 0.1, "affection": 0.7},
            "decay_per_hour": 0.05,
            "event_weights": {},
        },
        "availability": {
            "online_probability_by_hour": [0.5] * 24,
            "busy_probability_by_hour": [0.2] * 24,
            "reply_probability_when_seen": 0.8,
        },
    }


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    yield


@pytest.mark.asyncio
async def test_contacts_crud_and_trust_persistence():
    pid = "trust_test_persona"
    cid = "+1234567890"

    # Clean up before
    await delete_contact(pid, cid)

    # 1. Unknown contact returns None
    assert await get_contact(pid, cid) is None

    # 2. Add contact with trust
    saved = await save_or_update_contact(pid, cid, name="Alice", trust=0.25, source="whatsapp")
    assert saved["contact_id"] == cid
    assert saved["name"] == "Alice"
    assert saved["trust"] == pytest.approx(0.25)
    assert saved["source"] == "whatsapp"

    # 3. Retrieve contact
    loaded = await get_contact(pid, cid)
    assert loaded is not None
    assert loaded["name"] == "Alice"
    assert loaded["trust"] == pytest.approx(0.25)

    # 4. Update trust delta
    updated = await update_contact_trust(pid, cid, delta=0.1)
    assert updated["trust"] == pytest.approx(0.35)

    # Negative trust delta
    updated_neg = await update_contact_trust(pid, cid, delta=-0.5)
    assert updated_neg["trust"] == pytest.approx(-0.15)

    # Clamping test
    updated_clamp = await update_contact_trust(pid, cid, delta=2.0)
    assert updated_clamp["trust"] == pytest.approx(1.0)

    # 5. List contacts
    contacts = await list_contacts(pid)
    assert any(c["contact_id"] == cid for c in contacts)

    # 6. Delete contact
    assert await delete_contact(pid, cid) is True
    assert await get_contact(pid, cid) is None


@pytest.mark.asyncio
async def test_unknown_contact_reply_disabled_by_default(monkeypatch):
    persona_cfg = dummy_persona()
    redis = FakeRedis()
    service = ConversationService(persona_cfg, redis)

    # Ensure reply_unknown_contacts is False (default)
    monkeypatch.setattr(settings, "reply_unknown_contacts", False)

    msg = SocialMessage(
        session="trust_test_persona",
        message_id="msg_1",
        message_type="message",
        chat_id="unknown_user_999",
        item_id="item_1",
        sender_id="unknown_user_999",
        sender_name="Stranger",
        text="Hello there",
        provider="local",
    )

    result = await service.ingest(msg)
    assert "message_id" in result

    # When reply_unknown_contacts=False, no reply should be scheduled
    # Now enable reply_unknown_contacts=True
    monkeypatch.setattr(settings, "reply_unknown_contacts", True)
    result_enabled = await service.ingest(msg)
    assert "message_id" in result_enabled
