"""Conversation-owner persistence schema for the strict turn runtime."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


OWNER = "conversation"


class AtlasTurnConversationRow(OrmBase):
    __tablename__ = "atlas_turn_conversations"

    conversation_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    owner_actor_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    response_language: Mapped[str] = mapped_column(String(10), nullable=False)
    reasoning_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard"
    )
    next_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active','archived')", name="ck_atlas_turn_conversation_status"),
        CheckConstraint(
            "response_language IN ('zh-TW','en')",
            name="ck_atlas_turn_conversation_response_language",
        ),
        CheckConstraint(
            "reasoning_mode IN ('standard','deep')",
            name="ck_atlas_turn_conversation_reasoning_mode",
        ),
        CheckConstraint("next_ordinal >= 1", name="ck_atlas_turn_conversation_next_ordinal"),
    )


class AtlasTurnConversationMemberRow(OrmBase):
    __tablename__ = "atlas_turn_conversation_members"

    turn_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(200),
        ForeignKey("atlas_turn_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_atlas_turn_member_role"),
        CheckConstraint("ordinal >= 1", name="ck_atlas_turn_member_ordinal"),
        UniqueConstraint("conversation_id", "ordinal", name="uq_atlas_turn_member_ordinal"),
    )


class AtlasTurnConversationIdempotencyRow(OrmBase):
    __tablename__ = "atlas_turn_conversation_idempotency"

    scope_ref: Mapped[str] = mapped_column(String(500), primary_key=True)
    operation: Mapped[str] = mapped_column(String(40), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    response_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("operation IN ('create_conversation','create_turn','retry_turn')", name="ck_atlas_turn_conversation_idempotency_operation"),
        CheckConstraint("char_length(scope_ref) >= 1", name="ck_atlas_turn_conversation_idempotency_scope"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_conversation_idempotency_digest"),
    )


OWNER_TABLES = frozenset(
    {AtlasTurnConversationRow.__tablename__, AtlasTurnConversationMemberRow.__tablename__, AtlasTurnConversationIdempotencyRow.__tablename__}
)
