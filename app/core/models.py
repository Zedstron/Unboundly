from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String, Text, Index, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


Index("ix_conversation_messages_external", "source", "external_id")


class PersonaState(Base):
    __tablename__ = "persona_states"

    persona_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    mood: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    contact_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="Unknown")
    trust: Mapped[float] = mapped_column(default=0.0, nullable=False)
    relationship: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Durable memory of how much this contact has pestered the persona
    # (0.0 to 1.0, decays over time). Distinct from mood, which is transient.
    annoyance: Mapped[float] = mapped_column(default=0.0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


Index("ix_contacts_persona_contact", "persona_id", "contact_id", unique=True)


class CommitmentRow(Base):
    """Durable record of a promise the persona made to get back in touch.

    Redis carries the fast schedule, but a promise must survive a Redis
    restart/eviction, so the database is the source of truth: the worker
    atomically claims due rows, delivers, then settles them. ``status`` moves
    pending -> sending -> fulfilled | expired | cancelled.
    """

    __tablename__ = "commitments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="follow_up")
    window: Mapped[str] = mapped_column(String(30), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Transport routing metadata, resolved from the conversation at schedule
    # time so an autonomous follow-up goes back out the same bridge it came in
    # on (WhatsApp, Instagram, ...) rather than only the local inbox.
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


Index("ix_commitments_persona_status", "persona_id", "status")

