from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.models import ConversationMessage, PersonaState, Base

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
            ("sender_name", "VARCHAR(255)")
        ):
            if name not in columns:
                await connection.execute(text(f"ALTER TABLE conversation_messages ADD COLUMN {name} {definition}"))


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
    sender_name: str = "Unknown"
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
            sender_name=sender_name
        )

        session.add(message)

        await session.commit()
        await session.refresh(message)

        return message.id


async def get_message_by_external_id(source: str, external_id: str):
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage).where(
                ConversationMessage.source == source,
                ConversationMessage.external_id == external_id,
            ).limit(1)
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
                "sender_name": message.sender_name
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
