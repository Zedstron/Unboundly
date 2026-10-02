"""Regression tests for the echo bug and the memory-processing gap."""
import pytest

from app.domain.models import Decision
from app.infrastructure.sqlite import init_db
from app.services.conversation import ConversationService
from app.services.bridges.models import SocialMessage
from tests.test_contacts_and_trust import FakeRedis, dummy_persona


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    yield


def _msg(text="hey there"):
    return SocialMessage(
        session="trust_test_persona",
        message_id="m-1",
        message_type="message",
        chat_id="conv-1",
        item_id="i-1",
        sender_id="conv-1",
        sender_name="Tester",
        text=text,
        provider="local",
    )


def _service(monkeypatch, *, reply_unknown=True) -> ConversationService:
    from app.infrastructure.memory import ShortTermMemory

    monkeypatch.setattr(
        "app.services.conversation.settings.reply_unknown_contacts", reply_unknown
    )
    svc = ConversationService(dummy_persona(), FakeRedis())

    recorded = []
    original = ShortTermMemory.schedule

    async def spy_schedule(self, key, payload, due_at):
        recorded.append((key, dict(payload)))
        return await original(self, key, payload, due_at)

    monkeypatch.setattr(ShortTermMemory, "schedule", spy_schedule)
    svc._recorded_schedules = recorded
    return svc


def test_reply_task_payload_has_no_text():
    msg = _msg("my secret plans for friday")
    payload = ConversationService._reply_task_payload(msg, 42)

    assert "text" not in payload
    assert payload["user_message_id"] == 42
    assert payload["conversation_id"] == "conv-1"


def test_memory_task_payload_has_no_text():
    msg = _msg("remember this")
    payload = ConversationService._memory_task_payload(msg, 7)

    assert "text" not in payload
    assert payload["user_message_id"] == 7


@pytest.mark.asyncio
async def test_no_reply_still_schedules_memory_task(monkeypatch):
    svc = _service(monkeypatch)
    monkeypatch.setattr(
        svc.behavior, "decide", lambda state, ctx: Decision.NO_REPLY
    )

    await svc.ingest(_msg())

    keys = [k for k, _ in svc._recorded_schedules]
    assert "memory_pending" in keys
    assert "reply_pending" not in keys


@pytest.mark.asyncio
async def test_reply_now_does_not_embed_text(monkeypatch):
    svc = _service(monkeypatch)
    monkeypatch.setattr(
        svc.behavior, "decide", lambda state, ctx: Decision.REPLY_NOW
    )

    await svc.ingest(_msg("do not echo me"))

    scheduled = [(k, p) for k, p in svc._recorded_schedules if k == "reply_pending"]
    assert len(scheduled) == 1
    assert "text" not in scheduled[0][1]


@pytest.mark.asyncio
async def test_unknown_contact_gets_memory_task_no_reply(monkeypatch):
    svc = _service(monkeypatch, reply_unknown=False)

    result = await svc.ingest(_msg())

    assert "message_id" in result
    keys = [k for k, _ in svc._recorded_schedules]
    assert "memory_pending" in keys
    assert "reply_pending" not in keys
