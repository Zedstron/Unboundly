"""Tests for persona time-promise (commitment) scheduling.

Covers the resolver, the lenient response parsing, the durable journal, and the
ConversationService wiring that turns "I'll message you tonight" into an actual
follow-up.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models import AgentResponse, Commitment
from app.domain.scheduling import defer_within_window, resolve_schedule
from app.infrastructure.ai import parse_agent_response
from app.infrastructure.sqlite import (
    cancel_open_commitments,
    claim_due_commitments,
    clear_persona_commitments,
    clear_persona_messages,
    get_commitment,
    init_db,
    list_open_commitments,
    save_commitment,
    settle_commitment,
)
from app.infrastructure.memory import ShortTermMemory
from app.services.bridges.models import SocialMessage
from app.services.conversation import ConversationService
from tests.test_contacts_and_trust import FakeRedis, dummy_persona


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    # The SQLite file persists between runs; clear leftover promises so counts
    # from previous runs cannot leak into these assertions.
    await clear_persona_commitments("trust_test_persona")
    await clear_persona_commitments("commit_persona")
    yield


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_in_a_bit_resolves_soon_and_keeps_window():
    schedule = resolve_schedule("in_a_bit", now_local=NOW, rng=random.Random(1))

    assert NOW < schedule.due_at <= NOW + timedelta(minutes=40)
    assert schedule.window_end > schedule.due_at


def test_this_evening_lands_in_the_evening():
    schedule = resolve_schedule("this_evening", now_local=NOW, rng=random.Random(3))

    assert schedule.due_at.date() == NOW.date()
    assert 18 <= schedule.due_at.hour <= 20


def test_tonight_rolls_forward_when_passed():
    late = NOW.replace(hour=23, minute=30)

    schedule = resolve_schedule("tonight", now_local=late, rng=random.Random(5))

    assert schedule.due_at.date() == late.date() + timedelta(days=1)
    assert 21 <= schedule.due_at.hour <= 22


def test_tomorrow_morning_is_the_next_day():
    schedule = resolve_schedule("tomorrow_morning", now_local=NOW, rng=random.Random(2))

    assert schedule.due_at.date() == NOW.date() + timedelta(days=1)
    assert 8 <= schedule.due_at.hour <= 10


def test_later_today_never_spills_into_the_small_hours():
    late = NOW.replace(hour=23, minute=0)

    schedule = resolve_schedule("later_today", now_local=late, rng=random.Random(4))

    assert schedule.due_at.date() == late.date() + timedelta(days=1)
    assert schedule.due_at.hour >= 8


def test_resolution_is_deterministic_for_a_seed():
    first = resolve_schedule("tonight", now_local=NOW, rng=random.Random(42))
    second = resolve_schedule("tonight", now_local=NOW, rng=random.Random(42))

    assert first.due_at == second.due_at


def test_resolution_biases_toward_online_hours():
    def probability(hour: int) -> float:
        return 0.99 if hour == 20 else 0.01

    hits = sum(
        resolve_schedule(
            "this_evening",
            now_local=NOW,
            online_probability=probability,
            rng=random.Random(seed),
        ).due_at.hour
        for seed in range(40)
    )

    # With such a strong skew almost every draw should land on 20:00.
    assert hits >= 40 * 18


def test_defer_stays_inside_the_window():
    window_end = NOW + timedelta(minutes=8)

    deferred = defer_within_window(NOW, window_end, rng=random.Random(0))

    assert NOW < deferred < window_end


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_valid_commitment_is_parsed():
    parsed = parse_agent_response(
        '{"type": "text", "text": "busy, tonight", "reaction": null, '
        '"commitment": {"kind": "follow_up", "window": "tonight", "topic": "that thing"}}'
    )

    assert parsed.commitment is not None
    assert parsed.commitment.window == "tonight"
    assert parsed.commitment.topic == "that thing"


def test_invalid_commitment_is_dropped_not_fatal():
    parsed = parse_agent_response(
        '{"type": "text", "text": "I\'ll message you sometime", "reaction": null, '
        '"commitment": {"window": "someday"}}'
    )

    assert parsed.text == "I'll message you sometime"
    assert parsed.commitment is None


def test_agent_response_defaults_to_no_commitment():
    response = AgentResponse(type="text", text="hi")
    assert response.commitment is None
    assert Commitment(kind="deferred_reply", window="later_today").kind.value == "deferred_reply"


# ---------------------------------------------------------------------------
# Durable journal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commitment_journal_roundtrip_and_claim():
    pid, cid = "commit_persona", "conv-1"
    now = datetime.now(timezone.utc)

    record = await save_commitment(
        pid, cid,
        kind="follow_up", window="tonight", topic="dinner plans",
        due_at=now - timedelta(minutes=1), window_end=now + timedelta(hours=1),
        source="local", sender_id=cid, sender_name="Tester",
    )

    open_rows = await list_open_commitments(pid, cid)
    assert [row["id"] for row in open_rows] == [record["id"]]

    claimed = await claim_due_commitments(pid, now)
    assert [row["id"] for row in claimed] == [record["id"]]

    # A second claim must not re-take the row (atomic pending -> sending).
    assert await claim_due_commitments(pid, now) == []

    assert await settle_commitment(record["id"], "fulfilled") is True
    assert (await get_commitment(record["id"]))["status"] == "fulfilled"
    assert await list_open_commitments(pid, cid) == []


@pytest.mark.asyncio
async def test_cancel_open_commitments():
    pid, cid = "commit_persona", "conv-cancel"
    now = datetime.now(timezone.utc)
    await save_commitment(
        pid, cid, kind="follow_up", window="later_today", topic=None,
        due_at=now + timedelta(hours=2), window_end=now + timedelta(hours=4),
    )

    assert await cancel_open_commitments(pid, cid) == 1
    assert await list_open_commitments(pid, cid) == []


# ---------------------------------------------------------------------------
# ConversationService wiring
# ---------------------------------------------------------------------------


def _service(monkeypatch) -> ConversationService:
    monkeypatch.setattr(
        "app.services.conversation.settings.reply_unknown_contacts", True
    )
    svc = ConversationService(dummy_persona(), FakeRedis())

    recorded: list[tuple[str, dict]] = []
    original = ShortTermMemory.schedule

    async def spy_schedule(self, key, payload, due_at):
        recorded.append((key, dict(payload)))
        return await original(self, key, payload, due_at)

    monkeypatch.setattr(ShortTermMemory, "schedule", spy_schedule)
    svc._recorded_schedules = recorded
    return svc


@pytest.mark.asyncio
async def test_schedule_commitment_persists_and_supersedes(monkeypatch):
    svc = _service(monkeypatch)
    pid = svc.persona["id"]

    first = await svc.schedule_commitment(
        "conv-c", Commitment(window="tonight", topic="dinner"),
        source="local", sender_id="conv-c", sender_name="Tester",
    )
    second = await svc.schedule_commitment(
        "conv-c", Commitment(window="later_today", topic="call"),
        source="local", sender_id="conv-c", sender_name="Tester",
    )

    assert first is not None and second is not None
    assert (await get_commitment(first["id"]))["status"] == "cancelled"

    open_rows = await list_open_commitments(pid, "conv-c")
    assert [row["id"] for row in open_rows] == [second["id"]]


@pytest.mark.asyncio
async def test_process_due_commitments_queues_when_online(monkeypatch):
    svc = _service(monkeypatch)
    pid = svc.persona["id"]
    now = datetime.now(timezone.utc)

    await svc.short_memory.set_presence(pid, {"online": True, "busy": False})
    record = await save_commitment(
        pid, "conv-q", kind="follow_up", window="later_today", topic="call back",
        due_at=now - timedelta(seconds=5), window_end=now + timedelta(hours=2),
    )

    queued = await svc.process_due_commitments()

    assert queued == 1
    tasks = [p for k, p in svc._recorded_schedules if k == "commitment_reply"]
    assert tasks and tasks[0]["commitment_id"] == record["id"]
    assert "call back" in tasks[0]["commitment_context"]


@pytest.mark.asyncio
async def test_process_due_commitments_defers_when_offline(monkeypatch):
    svc = _service(monkeypatch)
    pid = svc.persona["id"]
    now = datetime.now(timezone.utc)

    await svc.short_memory.set_presence(pid, {"online": False, "busy": False})
    record = await save_commitment(
        pid, "conv-d", kind="follow_up", window="later_today", topic=None,
        due_at=now - timedelta(seconds=5), window_end=now + timedelta(hours=2),
    )

    queued = await svc.process_due_commitments()

    assert queued == 0
    after = await get_commitment(record["id"])
    assert after["status"] == "pending"
    assert after["due_at"] > record["due_at"]


@pytest.mark.asyncio
async def test_process_due_commitments_expires_after_window(monkeypatch):
    svc = _service(monkeypatch)
    pid = svc.persona["id"]
    now = datetime.now(timezone.utc)

    await svc.short_memory.set_presence(pid, {"online": False, "busy": False})
    record = await save_commitment(
        pid, "conv-e", kind="follow_up", window="later_today", topic=None,
        due_at=now - timedelta(hours=3), window_end=now - timedelta(hours=1),
    )

    await svc.process_due_commitments()

    assert (await get_commitment(record["id"]))["status"] == "expired"


@pytest.mark.asyncio
async def test_ingest_reply_cancels_standing_commitment(monkeypatch):
    from app.domain.models import Decision

    await clear_persona_messages("trust_test_persona")
    svc = _service(monkeypatch)
    pid = svc.persona["id"]
    now = datetime.now(timezone.utc)

    await save_commitment(
        pid, "conv-live", kind="follow_up", window="tonight", topic="plans",
        due_at=now + timedelta(hours=3), window_end=now + timedelta(hours=5),
    )
    monkeypatch.setattr(svc.behavior, "decide", lambda state, ctx: Decision.REPLY_NOW)

    async def _classify(_text):
        return "compliment"

    monkeypatch.setattr(svc, "_classify_event", _classify)

    message = SocialMessage(
        session=pid, message_id="m-live", message_type="message",
        chat_id="conv-live", item_id="i-1", sender_id="conv-live",
        sender_name="Tester", text="you there?", provider="local",
    )
    await svc.ingest(message)

    assert await list_open_commitments(pid, "conv-live") == []
