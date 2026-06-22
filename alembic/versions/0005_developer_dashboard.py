"""
alembic/versions/0005_developer_dashboard.py

Developer dashboard: API keys table, request_logs, support_tickets.

Up
──
  1. Create api_keys table (replaces single api_key_hash column on users)
  2. Create request_logs table
  3. Create support_tickets table
  4. Deprecate users.api_key_hash — rename to _legacy_api_key_hash so existing
     data isn't lost, then drop in 0006 once migration is confirmed.

Down
────
  Reverse all of the above. Legacy column is restored.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004_mock_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────────────────
    # Use the dialect-specific postgresql.ENUM (not the generic sa.Enum) with
    # create_type=False. Each enum is reused across multiple tables/columns
    # (e.g. keyenvironment on both api_keys and request_logs) — the generic
    # sa.Enum's create_type flag doesn't reliably survive the dialect-adapt
    # step that happens when attaching to a column, so SQLAlchemy still
    # tries to auto-CREATE TYPE on the second table that references it,
    # causing a DuplicateObjectError. postgresql.ENUM avoids that adapt step.
    keyenvironment = postgresql.ENUM("test", "live", name="keyenvironment", create_type=False)
    keystatus = postgresql.ENUM("active", "revoked", "expired", name="keystatus", create_type=False)
    ticketstatus = postgresql.ENUM("open", "in_progress", "resolved", "closed", name="ticketstatus", create_type=False)
    ticketpriority = postgresql.ENUM("low", "medium", "high", "critical", name="ticketpriority", create_type=False)
    ticketcategory = postgresql.ENUM(
        "api_keys", "transactions", "webhooks", "account", "billing", "bug", "other",
        name="ticketcategory", create_type=False,
    )

    for enum in (keyenvironment, keystatus, ticketstatus, ticketpriority, ticketcategory):
        enum.create(op.get_bind(), checkfirst=True)

    # ── api_keys ──────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(8), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("environment", keyenvironment, nullable=False, server_default="test"),
        sa.Column("status", keystatus, nullable=False, server_default="active"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])

    # ── request_logs ──────────────────────────────────────────────────────────
    op.create_table(
        "request_logs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("api_key_id", sa.String(26), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("environment", keyenvironment, nullable=False, server_default="test"),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("request_id", sa.String(26), nullable=True),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_request_logs_api_key_created", "request_logs", ["api_key_id", "created_at"])
    op.create_index("ix_request_logs_user_created", "request_logs", ["user_id", "created_at"])
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])
    op.create_index("ix_request_logs_request_id", "request_logs", ["request_id"])

    # ── support_tickets ───────────────────────────────────────────────────────
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("category", ticketcategory, nullable=False, server_default="other"),
        sa.Column("priority", ticketpriority, nullable=False, server_default="medium"),
        sa.Column("status", ticketstatus, nullable=False, server_default="open"),
        sa.Column("assigned_to", sa.String(26), nullable=True),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column("related_resource_type", sa.String(64), nullable=True),
        sa.Column("related_resource_id", sa.String(26), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])

    # ── Deprecate users.api_key_hash ──────────────────────────────────────────
    # Rename instead of dropping so data survives a rollback.
    # Drop in migration 0006 after confirmed stable.
    op.alter_column("users", "api_key_hash", new_column_name="_legacy_api_key_hash")


def downgrade() -> None:
    op.alter_column("users", "_legacy_api_key_hash", new_column_name="api_key_hash")

    op.drop_table("support_tickets")
    op.drop_index("ix_request_logs_request_id", "request_logs")
    op.drop_index("ix_request_logs_created_at", "request_logs")
    op.drop_index("ix_request_logs_user_created", "request_logs")
    op.drop_index("ix_request_logs_api_key_created", "request_logs")
    op.drop_table("request_logs")
    op.drop_index("ix_api_keys_prefix", "api_keys")
    op.drop_index("ix_api_keys_key_hash", "api_keys")
    op.drop_index("ix_api_keys_user_id", "api_keys")
    op.drop_table("api_keys")

    for name in ("keyenvironment", "keystatus", "ticketstatus", "ticketpriority", "ticketcategory"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)