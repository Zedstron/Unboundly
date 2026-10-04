"""Tests for social awareness: pester detection, annoyance, confidant selection."""
import pytest

from app.domain.awareness import (
    PesterLevel,
    annoyance_increment,
    decay_annoyance,
    evaluate_pestering,
    is_at_least,
    mood_event,
    select_confidant,
)
from app.domain.models import Decision
from app.infrastructure.sqlite import (
    clear_persona_messages,
    delete_contact,
    get_contact,
    init_db,
    list_contacts,
    save_or_update_contact,
    update_contact_annoyance,
)
from app.infrastructure.memory import ShortTermMemory
from app.services.conversation import ConversationService
from app.services.bridges.models import SocialMessage
from tests.test_contacts_and_trust import FakeRedis, dummy_persona

PERSONA_ID = "trust_test_persona"


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    yield


# --------------------------------------------------------------------------
# Domain unit tests
# --------------------------------------------------------------------------

def test_neutral_when_activity_is_low():
    activity = {"burst_2m": 1, "sustained_10m": 2, "hourly": 2, "daily": 3}
    assert evaluate_pestering(activity, 1) is PesterLevel.NEUTRAL


def test_burst_and_streak_raise_the_level():
    assert evaluate_pestering({"burst_2m": 3}, 0) is PesterLevel.NOTICE
    assert evaluate_pestering({}, 8) is PesterLevel.ANNOYED
    assert evaluate_pestering({}, 15) is PesterLevel.HARASSED


def test_known_contacts_are_treated_more_leniently():
    activity = {"burst_2m": 3, "sustained_10m": 5}
    assert evaluate_pestering(activity, 0, is_unknown=True) is PesterLevel.NOTICE
    assert evaluate_pestering(activity, 0, is_unknown=False) is PesterLevel.NEUTRAL


def test_custom_thresholds_override_defaults():
    assert evaluate_pestering({}, 2, thresholds={"annoyed_unanswered": 2}) is PesterLevel.ANNOYED


def test_mood_event_and_ordering():
    assert mood_event(PesterLevel.NEUTRAL) is None
    assert mood_event(PesterLevel.NOTICE) == "pestering"
    assert mood_event(PesterLevel.HARASSED) == "boundary_push"
    assert is_at_least(PesterLevel.ANNOYED, PesterLevel.ANNOYED)
    assert not is_at_least(PesterLevel.NOTICE, PesterLevel.ANNOYED)


def test_annoyance_increment_and_decay():
    assert annoyance_increment(PesterLevel.ANNOYED) > annoyance_increment(PesterLevel.NOTICE)
    assert decay_annoyance(0.5, 0) == pytest.approx(0.5)
    assert decay_annoyance(0.5, 100) == pytest.approx(0.0)


def test_select_confidant_prefers_trusted_known_contact():
    contacts = [
        {"contact_id": "stranger", "trust": 0.95, "is_unknown": True},
        {"contact_id": "friend", "trust": 0.4, "is_unknown": False},
        {"contact_id": "partner", "trust": 0.9, "is_unknown": False},
    ]
    chosen = select_confidant(contacts, min_trust=0.7)
    assert chosen["contact_id"] == "partner"
    # Excluding the only eligible contact leaves none at this floor.
    assert select_confidant(contacts, min_trust=0.7, exclude_id="partner") is None
    # A lower floor admits the simply-known friend.
    assert select_confidant(contacts, min_trust=0.3, exclude_id="partner")["contact_id"] == "friend"
    assert select_confidant(contacts, min_trust=0.99) is None


# --------------------------------------------------------------------------
# Integration: ingest schedules a confide task
# --------------------------------------------------------------------------

def _persona_with_awareness() -> dict:
    persona = dummy_persona()
    persona["social_awareness"] = {
        "enabled": True,
        "min_confidant_trust": 0.7,
        "time_windows": [],
        "confide_cooldown_minutes": 60,
        "confide_global_cooldown_minutes": 30,
        "daily_confide_budget": 2,
        "confide_delay_seconds": 0,
        "thresholds": {"annoyed_unanswered": 2},
    }
    persona["mood"]["event_weights"] = {
        "pestering": {"irritability": 0.08},
        "boundary_push": {"irritability": 0.16},
    }
    return persona


def _service(monkeypatch, persona=None) -> ConversationService:
    svc = ConversationService(persona or _persona_with_awareness(), FakeRedis())

    recorded = []
    original = ShortTermMemory.schedule

    async def spy_schedule(self, key, payload, due_at):
        recorded.append((key, dict(payload)))
        return await original(self, key, payload, due_at)

    monkeypatch.setattr(ShortTermMemory, "schedule", spy_schedule)
    svc._recorded_schedules = recorded
    return svc


async def _online():
    return {"online": True, "busy": False}


async def _classify(_text):
    return "compliment"


def _msg(external_id: str) -> SocialMessage:
    return SocialMessage(
        session=PERSONA_ID,
        message_id=external_id,
        message_type="message",
        chat_id="conv-1",
        item_id="i-1",
        sender_id="conv-1",
        sender_name="Stranger",
        text="hey",
        provider="whatsapp",
    )


async def _reset_contacts():
    for contact in await list_contacts(PERSONA_ID):
        await delete_contact(PERSONA_ID, contact["contact_id"])


@pytest.mark.asyncio
async def test_pestering_schedules_one_confide_to_confidant(monkeypatch):
    await clear_persona_messages(PERSONA_ID)
    await _reset_contacts()
    await save_or_update_contact(PERSONA_ID, "partner-1", name="Partner", trust=0.9, is_unknown=False)

    svc = _service(monkeypatch)
    monkeypatch.setattr(svc, "get_presence", _online)
    monkeypatch.setattr(svc, "_classify_event", _classify)
    monkeypatch.setattr("app.services.conversation.settings.reply_unknown_contacts", True)

    for index in range(3):
        await svc.ingest(_msg(f"pester-{index}"))

    confides = [payload for key, payload in svc._recorded_schedules if key == "confide"]
    assert len(confides) == 1
    assert confides[0]["conversation_id"] == "partner-1"
    assert confides[0]["trigger"] == "venting"
    assert confides[0]["offender_id"] == "conv-1"
    assert confides[0]["confide_context"]


@pytest.mark.asyncio
async def test_no_confide_without_eligible_confidant(monkeypatch):
    await clear_persona_messages(PERSONA_ID)
    await _reset_contacts()

    svc = _service(monkeypatch)
    monkeypatch.setattr(svc, "get_presence", _online)
    monkeypatch.setattr(svc, "_classify_event", _classify)
    monkeypatch.setattr("app.services.conversation.settings.reply_unknown_contacts", True)

    for index in range(3):
        await svc.ingest(_msg(f"lonely-{index}"))

    confides = [payload for key, payload in svc._recorded_schedules if key == "confide"]
    assert confides == []


@pytest.mark.asyncio
async def test_confide_is_rate_limited_after_first(monkeypatch):
    await clear_persona_messages(PERSONA_ID)
    await _reset_contacts()
    await save_or_update_contact(PERSONA_ID, "partner-1", name="Partner", trust=0.9, is_unknown=False)

    svc = _service(monkeypatch)
    monkeypatch.setattr(svc, "get_presence", _online)
    monkeypatch.setattr(svc, "_classify_event", _classify)
    monkeypatch.setattr("app.services.conversation.settings.reply_unknown_contacts", True)

    for index in range(8):
        await svc.ingest(_msg(f"spam-{index}"))

    confides = [payload for key, payload in svc._recorded_schedules if key == "confide"]
    assert len(confides) == 1


@pytest.mark.asyncio
async def test_annoyance_is_persisted():
    await _reset_contacts()
    await save_or_update_contact(PERSONA_ID, "nuisance", name="Nuisance", trust=0.0, is_unknown=True)

    await update_contact_annoyance(PERSONA_ID, "nuisance", 0.5)

    contact = await get_contact(PERSONA_ID, "nuisance")
    assert contact["annoyance"] == pytest.approx(0.5)
