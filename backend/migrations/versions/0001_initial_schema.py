"""Initial schema: tenants, users, documents, chunks, chat history and evaluation runs.

Also turns on row-level security for tenant data and grants the API role access.

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 1536

# The role the API connects as. It only gets data access, never schema changes.
APP_ROLE = "rka_app"

RLS_TABLES = ("documents", "chunks", "conversations", "messages", "feedback", "eval_runs")

# A row is visible only when its tenant_id matches the tenant set for the transaction.
# If no tenant is set, NULLIF turns the empty setting into NULL and no row matches.
TENANT_MATCH = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _id() -> sa.Column[sa.Uuid]:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _tenant_id() -> sa.Column[sa.Uuid]:
    return sa.Column("tenant_id", sa.Uuid(), nullable=False)


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["tenants.id"],
        name=op.f(f"fk_{table}_tenant_id_tenants"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenants",
        _id(),
        sa.Column("name", sa.String(200), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )

    op.create_table(
        "users",
        _id(),
        _tenant_id(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        _tenant_fk("users"),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.CheckConstraint("role IN ('admin', 'member')", name=op.f("ck_users_role_valid")),
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"])

    op.create_table(
        "api_keys",
        _id(),
        _tenant_id(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        _tenant_fk("api_keys"),
    )
    op.create_index(op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"])
    op.create_index(op.f("ix_api_keys_key_prefix"), "api_keys", ["key_prefix"])

    op.create_table(
        "documents",
        _id(),
        _tenant_id(),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        _tenant_fk("documents"),
        sa.UniqueConstraint(
            "tenant_id", "content_hash", name=op.f("uq_documents_tenant_id_content_hash")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name=op.f("ck_documents_status_valid"),
        ),
    )
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"])

    op.create_table(
        "chunks",
        _id(),
        _tenant_id(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(500), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("chunk_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        _tenant_fk("chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name=op.f("uq_chunks_document_id_chunk_index")
        ),
    )
    op.create_index(op.f("ix_chunks_tenant_id"), "chunks", ["tenant_id"])
    op.create_index(op.f("ix_chunks_document_id"), "chunks", ["document_id"])
    op.create_index(op.f("ix_chunks_chunk_hash"), "chunks", ["chunk_hash"])
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")

    op.create_table(
        "conversations",
        _id(),
        _tenant_id(),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        _tenant_fk("conversations"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"])
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"])

    op.create_table(
        "messages",
        _id(),
        _tenant_id(),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        _tenant_fk("messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_messages_role_valid")),
    )
    op.create_index(op.f("ix_messages_tenant_id"), "messages", ["tenant_id"])
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"])

    op.create_table(
        "feedback",
        _id(),
        _tenant_id(),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback")),
        _tenant_fk("feedback"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_feedback_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("message_id", name=op.f("uq_feedback_message_id")),
        sa.CheckConstraint("rating IN (-1, 1)", name=op.f("ck_feedback_rating_valid")),
    )
    op.create_index(op.f("ix_feedback_tenant_id"), "feedback", ["tenant_id"])

    op.create_table(
        "eval_runs",
        _id(),
        _tenant_id(),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
        _tenant_fk("eval_runs"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name=op.f("ck_eval_runs_status_valid"),
        ),
    )
    op.create_index(op.f("ix_eval_runs_tenant_id"), "eval_runs", ["tenant_id"])

    # Row-level security: the second lock on tenant data. FORCE makes the policy apply
    # to the table owner too (on managed hosts the owner is often not a superuser).
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_MATCH}) WITH CHECK ({TENANT_MATCH})"
        )

    # Data access for the API role, only if the role exists (it is created outside
    # migrations because it needs a password).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA public TO {APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
                    TO {APP_ROLE};
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE};
            END IF;
        END
        $$;
        """
    )
    for table in (
        "eval_runs",
        "feedback",
        "messages",
        "conversations",
        "chunks",
        "documents",
        "api_keys",
        "users",
        "tenants",
    ):
        op.drop_table(table)
    # The vector extension is left installed: other database objects may use it.
