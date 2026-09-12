from __future__ import annotations

from pathlib import Path
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.models import ConversationMessage, Base

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


async def save_message(persona_id: str, conversation_id: str, direction: Literal["user", "bot"], content: str, status: Literal["delivered", "seen"]) -> int:
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
            status=status
        )

        session.add(message)

        await session.commit()
        await session.refresh(message)

        return message.id


async def mark_message_seen(conversation_id: str, message_id: str) -> int:
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.id == message_id
            )
        )

        message = result.scalars().first()
        if message is None:
            return 0

        message.status = "seen"

        await session.commit()
        return message.id


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
    async with SessionFactory() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.id == message_id,
                ConversationMessage.direction == "user",
                ConversationMessage.status == "delivered",
            )
        )
        message = result.scalars().first()
        if message is None:
            return 0

        message.status = "seen"
        await session.commit()
        return message.id


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
