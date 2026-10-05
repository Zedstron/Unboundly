from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.models import CommitmentRow, ConversationMessage, PersonaState, Contact, Base


BASE_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = BASE_DIR / "personas" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DATA_DIR / 'conversations.db'}"

engine = create_async_engine(DATABASE_URL, echo=False)

SessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

        columns = {
            row[1] for row in (await connection.execute(text("PRAGMA table_info(conversation_messages)"))).all()
        }
        for name, definition in (
            ("source", "VARCHAR(30)"),
            ("external_id", "VARCHAR(255)"),
            ("sender_id", "VARCHAR(255)"),
            ("sender_name", "VARCHAR(255)"),
            ("reply_to_message_id", "VARCHAR(255)"),
            ("reply_to_text", "TEXT")
        ):
            if name not in columns:
                await connection.execute(text(f"ALTER TABLE conversation_messages ADD COLUMN {name} {definition}"))

        contact_columns = {
            row[1] for row in (await connection.execute(text("PRAGMA table_info(contacts)"))).all()
        }
        if "is_unknown" not in contact_columns:
            await connection.execute(text("ALTER TABLE contacts ADD COLUMN is_unknown BOOLEAN NOT NULL DEFAULT 1"))
            # Backfill: contacts that already have a real name were vetted; the
            # rest stay unknown so unvetted contacts are not auto-replied to.
            await connection.execute(text(
                "UPDATE contacts SET is_unknown = 0 "
                "WHERE name IS NOT NULL AND TRIM(name) <> '' AND LOWER(TRIM(name)) <> 'unknown'"
            ))
        if "relationship" not in contact_columns:
            # Existing contacts keep NULL; the relationship is recomputed from
            # trust on read until the next explicit save rewrites it.
            await connection.execute(text("ALTER TABLE contacts ADD COLUMN relationship VARCHAR(30)"))
        if "annoyance" not in contact_columns:
            await connection.execute(text("ALTER TABLE contacts ADD COLUMN annoyance FLOAT NOT NULL DEFAULT 0"))

    # Backfill the stored relationship stage for legacy rows so the
    # column is always populated on read. Runs through the async session
    # because run_sync callbacks expect sync-style connection usage.
    await _backfill_relationships()


async def save_message(
    persona_id: str,
    conversation_id: str,
    direction: Literal["user", "bot"],
    content: str,
    status: Literal["delivered", "seen"],
    *,
    source: str | None = None,
    external_id: str | None = None,
    sender_id: str | None = None,
    sender_name: str = "Unknown",
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
) -> int:
    if direction not in ("user", "bot"):
        raise ValueError("direction must be either 'user' or 'bot'")

    if status not in ("delivered", "seen"):
        raise ValueError("direction must be either 'delivered' or 'seen'")

    async with SessionFactory() as session:
        message = ConversationMessage(
            persona_id=persona_id,
            conversation_id=conversation_id,
            direction=direction,
            content=content,
            status=status,
            source=source,
            external_id=external_id,
            sender_id=sender_id,
            sender_name=sender_name,
            reply_to_message_id=reply_to_message_id,
            reply_to_text=reply_to_text,
        )

        session.add(message)

        await session.commit()
        await session.refresh(message)

        return message.id


async def get_message_by_external_id(
    source: str,
    external_id: str,
    persona_id: str | None = None,
    direction: str | None = None,
):
    """Look up a stored message by its provider id.

    Used to make ingestion idempotent: a bridge that re-emits (or replays)
    the same inbound message must not create a second conversation row and a
    second reply. ``persona_id``/``direction`` narrow the match when known.
    """
    query = select(ConversationMessage).where(
        ConversationMessage.source == source,
        ConversationMessage.external_id == external_id,
    )
    if persona_id is not None:
        query = query.where(ConversationMessage.persona_id == persona_id)
    if direction is not None:
        query = query.where(ConversationMessage.direction == direction)

    async with SessionFactory() as session:
        result = await session.execute(query.limit(1))
        return result.scalars().first()


async def get_external_id(message_id: int) -> str | None:
    """The provider message id for a stored message, used as a reply target."""
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage.external_id).where(
                ConversationMessage.id == message_id
            )
        )
        return result.scalars().first()


async def set_external_id(message_id: int, source: str, external_id: str) -> bool:
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage).where(ConversationMessage.id == message_id).limit(1)
        )
        message = result.scalars().first()
        if message is None:
            return False
        message.source = source
        message.external_id = external_id
        await session.commit()
        return True


async def mark_messages_seen(
    conversation_id: str,
    persona_id: str | None = None,
    up_to_message_id: int | None = None,
) -> list[int]:
    """Mark unread user messages as seen for a conversation.

    If up_to_message_id is provided, only messages with id <= up_to_message_id
    are marked. Otherwise all unread user messages in the conversation are marked.
    Returns the list of message IDs that were transitioned to 'seen'.
    """
    async with SessionFactory() as session:
        query = select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.direction == "user",
            ConversationMessage.status != "seen",
        )
        if persona_id is not None:
            query = query.where(ConversationMessage.persona_id == persona_id)
        if up_to_message_id is not None:
            query = query.where(ConversationMessage.id <= up_to_message_id)

        query = query.order_by(ConversationMessage.id.asc())
        result = await session.execute(query)
        messages = result.scalars().all()
        if not messages:
            return []

        updated_ids = []
        for message in messages:
            message.status = "seen"
            updated_ids.append(message.id)

        await session.commit()
        return updated_ids


async def mark_message_seen(conversation_id: str, message_id: str | int) -> int:
    try:
        mid = int(message_id)
    except (ValueError, TypeError):
        mid = None

    updated = await mark_messages_seen(conversation_id, up_to_message_id=mid)
    if mid is not None and mid in updated:
        return mid
    if updated:
        return updated[-1]
    if mid is not None:
        async with SessionFactory() as session:
            result = await session.execute(
                select(ConversationMessage.id).where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.id == mid,
                )
            )
            found = result.scalars().first()
            if found is not None:
                return found
    return 0


async def get_unread_user_messages(persona_id: str) -> list[dict]:
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.direction == "user",
                ConversationMessage.status == "delivered",
            )
            .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
        )

        return [
            {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "content": message.content,
                "created_at": message.created_at,
                "source": message.source,
                "sender_id": message.sender_id,
                "sender_name": message.sender_name,
                "external_id": message.external_id,
                "reply_to_message_id": message.reply_to_message_id,
                "reply_to_text": message.reply_to_text,
            }
            for message in result.scalars().all()
        ]


async def get_inactive_conversations(
    persona_id: str,
    inactive_since,
) -> list[dict]:
    """Return conversations whose latest human message predates a cutoff."""
    async with SessionFactory() as session:
        result = await session.execute(
            select(
                ConversationMessage.conversation_id,
                func.max(ConversationMessage.created_at).label("last_user_at"),
            )
            .where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.direction == "user",
            )
            .group_by(ConversationMessage.conversation_id)
            .having(func.max(ConversationMessage.created_at) <= inactive_since)
        )
        return [
            {"conversation_id": row.conversation_id, "last_user_at": row.last_user_at}
            for row in result.all()
        ]


async def mark_message_seen_for_persona(
    persona_id: str,
    conversation_id: str,
    message_id: int,
) -> int:
    updated = await mark_messages_seen(
        conversation_id,
        persona_id=persona_id,
        up_to_message_id=message_id,
    )
    if message_id in updated:
        return message_id
    if updated:
        return updated[-1]
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage.id).where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.id == message_id,
                ConversationMessage.direction == "user",
            )
        )
        found = result.scalars().first()
        if found is not None:
            return found
    return 0


async def get_conversation(persona_id: str, conversation_id: str, limit: int | None = None) -> list[dict]:
    async with SessionFactory() as session:
        query = select(ConversationMessage).where(
            ConversationMessage.persona_id == persona_id,
            ConversationMessage.conversation_id == conversation_id
        )

        if limit is not None:
            query = query.order_by(ConversationMessage.created_at.desc()).limit(limit)
            result = await session.execute(query)
            messages = list(reversed(result.scalars().all()))
        else:
            query = query.order_by(ConversationMessage.created_at.asc())
            result = await session.execute(query)
            messages = result.scalars().all()

        return [
            {
                "id": message.id,
                "direction": message.direction,
                "content": message.content,
                "status": message.status,
                "created_at": message.created_at.isoformat(),
                "reply_to_message_id": message.reply_to_message_id,
                "reply_to_text": message.reply_to_text,
            }
            for message in messages
        ]

async def delete_message(persona_id: str, conversation_id: str, message_id: int) -> bool:
    async with SessionFactory() as session:
        result = await session.execute(
            delete(ConversationMessage).where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.id == message_id,
            )
        )
        await session.commit()
        return result.rowcount > 0

async def clear_conversation(persona_id: str, conversation_id: str) -> int:
    async with SessionFactory() as session:
        result = await session.execute(
            delete(ConversationMessage).where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id,
            )
        )
        await session.commit()
        return result.rowcount


async def clear_persona_messages(persona_id: str, conversation_id: str | None = None) -> int:
    async with SessionFactory() as session:
        query = delete(ConversationMessage).where(ConversationMessage.persona_id == persona_id)
        if conversation_id is not None:
            query = query.where(ConversationMessage.conversation_id == conversation_id)
        result = await session.execute(query)
        await session.commit()
        return result.rowcount


async def get_persona_conversations(persona_id: str) -> list[dict[str, Any]]:
    async with SessionFactory() as session:
        result = await session.execute(
            select(
                ConversationMessage.conversation_id,
                func.count(ConversationMessage.id).label("message_count"),
                func.max(ConversationMessage.created_at).label("last_message_at"),
            )
            .where(ConversationMessage.persona_id == persona_id)
            .group_by(ConversationMessage.conversation_id)
            .order_by(func.max(ConversationMessage.created_at).desc(), ConversationMessage.conversation_id.asc())
        )
        conversations: list[dict[str, Any]] = []
        for row in result.all():
            last_message = await session.execute(
                select(ConversationMessage.content)
                .where(
                    ConversationMessage.persona_id == persona_id,
                    ConversationMessage.conversation_id == row.conversation_id,
                )
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
                .limit(1)
            )
            content = last_message.scalars().first()
            conversations.append({
                "conversation_id": row.conversation_id,
                "message_count": int(row.message_count),
                "last_message": content,
                "last_message_at": row.last_message_at,
            })
        return conversations


async def get_last_message(persona_id: str, conversation_id: str) -> dict:
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id
            )
            .order_by(
                ConversationMessage.created_at.desc(),
                ConversationMessage.id.desc(),
            )
            .limit(1)
        )

        return result.scalars().first()


async def get_last_user_message(persona_id: str, conversation_id: str) -> dict[str, Any] | None:
    """Return the most recent inbound message's routing metadata for a conversation."""
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.direction == "user",
            )
            .order_by(
                ConversationMessage.created_at.desc(),
                ConversationMessage.id.desc(),
            )
            .limit(1)
        )
        message = result.scalars().first()
        if message is None:
            return None

        return {
            "id": message.id,
            "content": message.content,
            "source": message.source,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "external_id": message.external_id,
            "reply_to_message_id": message.reply_to_message_id,
            "reply_to_text": message.reply_to_text,
            "created_at": message.created_at,
        }


async def save_persona_state(
    persona_id: str,
    mood: dict[str, float],
    updated_at: datetime | None = None,
) -> None:
    if updated_at is None:
        updated_at = datetime.now(timezone.utc)
    elif updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    async with SessionFactory() as session:
        result = await session.execute(
            select(PersonaState).where(PersonaState.persona_id == persona_id).limit(1)
        )
        state_row = result.scalars().first()
        if state_row is None:
            state_row = PersonaState(
                persona_id=persona_id,
                mood=mood,
                updated_at=updated_at,
            )
            session.add(state_row)
        else:
            state_row.mood = mood
            state_row.updated_at = updated_at
        await session.commit()


async def get_persona_state(persona_id: str) -> dict[str, Any] | None:
    async with SessionFactory() as session:
        result = await session.execute(
            select(PersonaState).where(PersonaState.persona_id == persona_id).limit(1)
        )
        state_row = result.scalars().first()
        if state_row is None:
            return None

        updated_at = state_row.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        return {
            "persona_id": state_row.persona_id,
            "mood": {str(k): float(v) for k, v in state_row.mood.items()},
            "updated_at": updated_at,
        }


async def reset_persona_state(persona_id: str) -> bool:
    async with SessionFactory() as session:
        result = await session.execute(
            delete(PersonaState).where(PersonaState.persona_id == persona_id)
        )
        await session.commit()
        return result.rowcount > 0


async def get_all_persona_states() -> list[dict[str, Any]]:
    async with SessionFactory() as session:
        result = await session.execute(select(PersonaState))
        rows = result.scalars().all()
        states = []
        for r in rows:
            updated_at = r.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            states.append({
                "persona_id": r.persona_id,
                "mood": {str(k): float(v) for k, v in r.mood.items()},
                "updated_at": updated_at,
            })
        return states


async def get_contact(persona_id: str, contact_id: str) -> dict[str, Any] | None:
    async with SessionFactory() as session:
        result = await session.execute(
            select(Contact).where(
                Contact.persona_id == persona_id,
                Contact.contact_id == contact_id,
            ).limit(1)
        )
        row = result.scalars().first()
        if row is None:
            return None

        return await _contact_row_dict(row)


def _default_is_unknown(name: str | None) -> bool:
    """A name that is missing or the placeholder means the contact is unvetted."""
    return not name or name.strip().lower() == "unknown"


def _stage_for_trust_value(trust: float) -> str:
    from app.domain.relationship import RelationshipStage, stage_for_trust

    return stage_for_trust(trust).value


async def _backfill_relationships() -> None:
    """Backfill relationship stages for legacy contact rows missing one."""
    from app.domain.relationship import stage_for_trust

    async with SessionFactory() as session:
        result = await session.execute(
            select(Contact).where(Contact.relationship.is_(None))
        )
        rows = result.scalars().all()
        for row in rows:
            row.relationship = stage_for_trust(float(row.trust)).value
        if rows:
            await session.commit()


async def _contact_row_dict(row: Contact) -> dict[str, Any]:
    from app.domain.relationship import RelationshipStage, stage_for_trust, relationship_payload

    stored = (row.relationship or "").strip()
    stage_value = stored or stage_for_trust(float(row.trust)).value
    try:
        RelationshipStage(stage_value)
    except ValueError:
        stage_value = stage_for_trust(float(row.trust)).value

    payload = relationship_payload(float(row.trust))
    return {
        "id": row.id,
        "persona_id": row.persona_id,
        "contact_id": row.contact_id,
        "name": row.name,
        "trust": float(row.trust),
        "relationship": stage_value,
        "source": row.source,
        "is_unknown": bool(row.is_unknown),
        "annoyance": float(row.annoyance or 0.0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "relationship_payload": payload,
    }


async def save_or_update_contact(
    persona_id: str,
    contact_id: str,
    name: str = "Unknown",
    trust: float = 0.0,
    source: str | None = None,
    is_unknown: bool | None = None,
    relationship: str | None = None,
) -> dict[str, Any]:
    """Explicitly create/update a contact.

    When ``is_unknown`` is omitted it is inferred for new contacts from the
    name (a real name marks the contact as known) and preserved for existing
    contacts so an explicit save does not silently flip its status. When
    ``relationship`` is omitted the stage is derived from trust; passing a
    valid stage name pins it (e.g. "family" or "blocked").
    """
    now = datetime.now(timezone.utc)
    clamped_trust = max(-1.0, min(1.0, float(trust)))
    from app.domain.relationship import RelationshipStage

    async with SessionFactory() as session:
        result = await session.execute(
            select(Contact).where(
                Contact.persona_id == persona_id,
                Contact.contact_id == contact_id,
            ).limit(1)
        )
        row = result.scalars().first()
        if row is None:
            row = Contact(
                persona_id=persona_id,
                contact_id=contact_id,
                name=name,
                trust=clamped_trust,
                relationship=relationship or _stage_for_trust_value(clamped_trust),
                source=source,
                is_unknown=_default_is_unknown(name) if is_unknown is None else bool(is_unknown),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            if name and name != "Unknown":
                row.name = name
            row.trust = clamped_trust
            if relationship:
                # Preserve an explicit value even if it disagrees with trust:
                # admins can pin e.g. "family" or "blocked" manually.
                try:
                    RelationshipStage(relationship)
                    row.relationship = relationship
                except ValueError:
                    pass
            if source:
                row.source = source
            if is_unknown is not None:
                row.is_unknown = bool(is_unknown)
            row.updated_at = now

        await session.commit()
        await session.refresh(row)
        return await _contact_row_dict(row)


async def update_contact_trust(
    persona_id: str,
    contact_id: str,
    delta: float,
    name: str | None = None,
    source: str | None = None,
    is_unknown: bool | None = None,
    relationship: str | None = None,
) -> dict[str, Any]:
    """Adjust a contact's trust after an interaction.

    This also runs for contacts the persona already replied to, so newly
    created rows stay flagged ``is_unknown=True`` (and existing rows keep
    their status) unless the caller explicitly overrides it. That prevents a
    reply from silently un-vetting a contact.
    """
    now = datetime.now(timezone.utc)
    async def _update() -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with SessionFactory() as session:
            result = await session.execute(
                select(Contact).where(
                    Contact.persona_id == persona_id,
                    Contact.contact_id == contact_id,
                ).limit(1)
            )
            row = result.scalars().first()
            if row is None:
                new_trust = max(-1.0, min(1.0, float(delta)))
                row = Contact(
                    persona_id=persona_id,
                    contact_id=contact_id,
                    name=name or "Unknown",
                    trust=new_trust,
                    relationship=_stage_for_trust_value(new_trust),
                    source=source,
                    is_unknown=True if is_unknown is None else bool(is_unknown),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                new_trust = max(-1.0, min(1.0, float(row.trust) + float(delta)))
                row.trust = new_trust
                # Trust drives the stage unless the stage was pinned manually
                # (or the caller explicitly pinned one just now).
                if (row.relationship or "") not in _PINNED_STAGES:
                    row.relationship = _stage_for_trust_value(new_trust)
                if relationship:
                    try:
                        RelationshipStage(relationship)
                        row.relationship = relationship
                    except ValueError:
                        pass
                if name and name != "Unknown":
                    row.name = name
                if source:
                    row.source = source
                if is_unknown is not None:
                    row.is_unknown = bool(is_unknown)
                row.updated_at = now

            await session.commit()
            await session.refresh(row)
            return await _contact_row_dict(row)

    from app.domain.relationship import RelationshipStage

    _PINNED_STAGES = {RelationshipStage.FAMILY.value, RelationshipStage.BLOCKED.value}

    return await _update()


async def update_contact_annoyance(
    persona_id: str,
    contact_id: str,
    annoyance: float,
) -> dict[str, Any] | None:
    """Set a contact's pester-annoyance score (clamped to 0..1)."""
    clean = max(0.0, min(1.0, float(annoyance)))
    async with SessionFactory() as session:
        result = await session.execute(
            select(Contact).where(
                Contact.persona_id == persona_id,
                Contact.contact_id == contact_id,
            ).limit(1)
        )
        row = result.scalars().first()
        if row is None:
            return None
        row.annoyance = clean
        await session.commit()
        await session.refresh(row)
        return await _contact_row_dict(row)


async def count_unanswered_user_messages(
    persona_id: str,
    conversation_id: str,
) -> int:
    """Inbound messages received since the persona's last reply in a thread."""
    async with SessionFactory() as session:
        last_bot_id = (await session.execute(
            select(func.max(ConversationMessage.id)).where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.direction == "bot",
            )
        )).scalar()

        query = select(func.count()).select_from(ConversationMessage).where(
            ConversationMessage.persona_id == persona_id,
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.direction == "user",
        )
        if last_bot_id is not None:
            query = query.where(ConversationMessage.id > last_bot_id)

        return int((await session.execute(query)).scalar() or 0)


async def find_conversation_by_sender(
    persona_id: str,
    sender_id: str,
) -> str | None:
    """Most recent conversation a given sender has messaged the persona in.

    Needed to route a proactive message to a contact whose contact id
    (Instagram user id) differs from the conversation/thread id.
    """
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage.conversation_id)
            .where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.direction == "user",
                ConversationMessage.sender_id == sender_id,
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(1)
        )
        return result.scalars().first()


async def list_contacts(persona_id: str | None = None) -> list[dict[str, Any]]:
    async with SessionFactory() as session:
        query = select(Contact)
        if persona_id is not None:
            query = query.where(Contact.persona_id == persona_id)
        query = query.order_by(Contact.name.asc(), Contact.id.asc())
        result = await session.execute(query)
        rows = result.scalars().all()
        return [await _contact_row_dict(r) for r in rows]


async def delete_contact(persona_id: str, contact_id: str) -> bool:
    async with SessionFactory() as session:
        result = await session.execute(
            delete(Contact).where(
                Contact.persona_id == persona_id,
                Contact.contact_id == contact_id,
            )
        )
        await session.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Commitments: durable promises the persona made to get back in touch.
# ---------------------------------------------------------------------------


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _commitment_row_dict(row: CommitmentRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "persona_id": row.persona_id,
        "conversation_id": row.conversation_id,
        "kind": row.kind,
        "window": row.window,
        "topic": row.topic,
        "status": row.status,
        "due_at": _as_utc(row.due_at),
        "window_end": _as_utc(row.window_end),
        "attempts": int(row.attempts or 0),
        "source": row.source,
        "sender_id": row.sender_id,
        "sender_name": row.sender_name,
        "created_at": _as_utc(row.created_at),
        "updated_at": _as_utc(row.updated_at),
    }


async def save_commitment(
    persona_id: str,
    conversation_id: str,
    *,
    kind: str,
    window: str,
    topic: str | None,
    due_at: datetime,
    window_end: datetime,
    source: str | None = None,
    sender_id: str | None = None,
    sender_name: str | None = None,
) -> dict[str, Any]:
    """Persist a newly promised follow-up."""
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        row = CommitmentRow(
            persona_id=persona_id,
            conversation_id=conversation_id,
            kind=kind,
            window=window,
            topic=topic,
            status="pending",
            due_at=_as_utc(due_at),
            window_end=_as_utc(window_end),
            attempts=0,
            source=source,
            sender_id=sender_id,
            sender_name=sender_name,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _commitment_row_dict(row)


async def get_commitment(commitment_id: int) -> dict[str, Any] | None:
    async with SessionFactory() as session:
        result = await session.execute(
            select(CommitmentRow).where(CommitmentRow.id == commitment_id).limit(1)
        )
        row = result.scalars().first()
        return _commitment_row_dict(row) if row is not None else None


async def list_open_commitments(
    persona_id: str,
    conversation_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Promises still standing, soonest first — the persona's own to-do list.

    Used to feed generation so the persona does not contradict a promise it
    has already made.
    """
    async with SessionFactory() as session:
        query = select(CommitmentRow).where(
            CommitmentRow.persona_id == persona_id,
            CommitmentRow.status.in_(("pending", "sending")),
        )
        if conversation_id is not None:
            query = query.where(CommitmentRow.conversation_id == conversation_id)
        query = query.order_by(CommitmentRow.due_at.asc()).limit(limit)
        rows = (await session.execute(query)).scalars().all()
        return [_commitment_row_dict(row) for row in rows]


async def claim_due_commitments(
    persona_id: str,
    now: datetime | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Atomically take ownership of due promises for one persona.

    The guarded ``UPDATE ... WHERE status='pending'`` makes the claim safe
    across concurrent workers: only one caller flips a row to ``sending``.
    """
    now = _as_utc(now) or datetime.now(timezone.utc)
    claimed: list[dict[str, Any]] = []
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(CommitmentRow)
                .where(
                    CommitmentRow.persona_id == persona_id,
                    CommitmentRow.status == "pending",
                    CommitmentRow.due_at <= now,
                )
                .order_by(CommitmentRow.due_at.asc())
                .limit(limit)
            )
        ).scalars().all()

        for row in rows:
            result = await session.execute(
                update(CommitmentRow)
                .where(
                    CommitmentRow.id == row.id,
                    CommitmentRow.status == "pending",
                )
                .values(
                    status="sending",
                    attempts=CommitmentRow.attempts + 1,
                    updated_at=now,
                )
            )
            if result.rowcount:
                fresh = (await session.execute(
                    select(CommitmentRow).where(CommitmentRow.id == row.id)
                )).scalars().first()
                if fresh is not None:
                    claimed.append(_commitment_row_dict(fresh))

        await session.commit()
    return claimed


async def reschedule_commitment(commitment_id: int, due_at: datetime) -> bool:
    """Return a claimed promise to the queue at a later instant within its window."""
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        result = await session.execute(
            update(CommitmentRow)
            .where(
                CommitmentRow.id == commitment_id,
                CommitmentRow.status.in_(("pending", "sending")),
            )
            .values(status="pending", due_at=_as_utc(due_at), updated_at=now)
        )
        await session.commit()
        return result.rowcount > 0


async def settle_commitment(commitment_id: int, status: str) -> bool:
    """Finalize a commitment (``fulfilled``, ``expired`` or ``cancelled``)."""
    if status not in ("fulfilled", "expired", "cancelled"):
        raise ValueError(f"invalid commitment status: {status}")
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        result = await session.execute(
            update(CommitmentRow)
            .where(
                CommitmentRow.id == commitment_id,
                CommitmentRow.status.in_(("pending", "sending")),
            )
            .values(status=status, updated_at=now)
        )
        await session.commit()
        return result.rowcount > 0


async def cancel_open_commitments(persona_id: str, conversation_id: str) -> int:
    """Cancel standing promises for a conversation (persona answered first)."""
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        result = await session.execute(
            update(CommitmentRow)
            .where(
                CommitmentRow.persona_id == persona_id,
                CommitmentRow.conversation_id == conversation_id,
                CommitmentRow.status.in_(("pending", "sending")),
            )
            .values(status="cancelled", updated_at=now)
        )
        await session.commit()
        return int(result.rowcount or 0)


async def clear_persona_commitments(
    persona_id: str,
    conversation_id: str | None = None,
) -> int:
    """Delete a persona's commitment rows (optionally one conversation)."""
    async with SessionFactory() as session:
        query = delete(CommitmentRow).where(CommitmentRow.persona_id == persona_id)
        if conversation_id is not None:
            query = query.where(CommitmentRow.conversation_id == conversation_id)
        result = await session.execute(query)
        await session.commit()
        return int(result.rowcount or 0)


async def recover_stale_commitments(
    persona_id: str,
    stale_before: datetime,
) -> int:
    """Re-queue commitments stuck in ``sending`` (worker crashed mid-delivery).

    Gives at-least-once delivery for promises; a rare duplicate follow-up is
    preferable to a promise silently never being kept.
    """
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        result = await session.execute(
            update(CommitmentRow)
            .where(
                CommitmentRow.persona_id == persona_id,
                CommitmentRow.status == "sending",
                CommitmentRow.updated_at <= _as_utc(stale_before),
            )
            .values(status="pending", updated_at=now)
        )
        await session.commit()
        return int(result.rowcount or 0)

