"""Database tables.

Every table holding a company's data has a ``tenant_id`` column. The tables listed in
``RLS_TABLES`` are also protected by Postgres row-level security (see the first
migration), so a query that forgets to filter by tenant still can't read another
tenant's rows.

``users`` and ``api_keys`` are left out of row-level security on purpose: they are
looked up *before* we know the tenant (by email at login, by key hash for API calls).
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, TenantOwned, UUIDPrimaryKey

# Must match the embedding model's output size. Changing it needs a migration.
EMBEDDING_DIMENSIONS = 1536

RLS_TABLES = ("documents", "chunks", "conversations", "messages", "feedback", "eval_runs")


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class DocumentStatus(enum.StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class EvalRunStatus(enum.StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _one_of(column: str, values: type[enum.StrEnum]) -> str:
    options = ", ".join(f"'{v.value}'" for v in values)
    return f"{column} IN ({options})"


class Tenant(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))


class User(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_one_of("role", UserRole), name="role_valid"),)

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=UserRole.MEMBER)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class ApiKey(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(100))
    # The first characters of the key, kept in plain text so a key can be found and shown
    # ("ds_ab12...") without storing the key itself.
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_hash"),
        CheckConstraint(_one_of("status", DocumentStatus), name="status_valid"),
    )

    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    # SHA-256 of the file, so uploading the same file twice is detected.
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=DocumentStatus.QUEUED)
    page_count: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chunk(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int]
    page: Mapped[int]
    section: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int]
    chunk_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Kept up to date by Postgres itself, so keyword search never drifts from the text.
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True), nullable=True
    )


class Conversation(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint(_one_of("role", MessageRole), name="role_valid"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None]


class Feedback(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "feedback"
    __table_args__ = (CheckConstraint("rating IN (-1, 1)", name="rating_valid"),)

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)


class EvalRun(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    __tablename__ = "eval_runs"
    __table_args__ = (CheckConstraint(_one_of("status", EvalRunStatus), name="status_valid"),)

    status: Mapped[str] = mapped_column(String(16), default=EvalRunStatus.RUNNING)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
