"""add mock bank ledger tables

Revision ID: 0003_mock_bank
Revises: 0002_add_wallet_id
Create Date: 2026-06-18

"""
from __future__ import annotations

from alembic import op

revision = "0003_mock_bank"
down_revision = "0002_add_wallet_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE mockaccountstatus AS ENUM ('active', 'frozen', 'closed')"
    )
    op.execute(
        "CREATE TYPE mockentrytype AS ENUM ('credit', 'debit')"
    )
    op.execute(
        "CREATE TYPE mocktransferstatus AS ENUM ('pending', 'success', 'failed')"
    )
    op.execute(
        "CREATE TYPE mockwebhookstatus AS ENUM ('pending', 'delivered', 'failed')"
    )

    op.execute("""
    CREATE TABLE mock_virtual_accounts (
        id VARCHAR(26) PRIMARY KEY,
        account_number VARCHAR(10) NOT NULL UNIQUE,
        bank_code VARCHAR(10) NOT NULL DEFAULT '999',
        bank_name VARCHAR(64) NOT NULL DEFAULT 'MockBank MFB',
        account_name VARCHAR(128) NOT NULL,
        customer_ref VARCHAR(64) NOT NULL,
        currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
        balance NUMERIC(18, 4) NOT NULL DEFAULT 0,
        status mockaccountstatus NOT NULL DEFAULT 'active',
        metadata_json TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    op.execute(
        "CREATE INDEX ix_mock_va_account_number ON mock_virtual_accounts (account_number)"
    )

    op.execute(
        "CREATE INDEX ix_mock_va_customer_ref ON mock_virtual_accounts (customer_ref)"
    )

    op.execute("""
    CREATE TABLE mock_ledger_entries (
        id VARCHAR(26) PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        account_id VARCHAR(26) NOT NULL REFERENCES mock_virtual_accounts(id) ON DELETE RESTRICT,
        entry_type mockentrytype NOT NULL,
        amount NUMERIC(18, 4) NOT NULL,
        balance_before NUMERIC(18, 4) NOT NULL,
        balance_after NUMERIC(18, 4) NOT NULL,
        reference VARCHAR(64) NOT NULL,
        description TEXT
    )
    """)

    op.execute(
        "CREATE INDEX ix_mock_ledger_account_id ON mock_ledger_entries (account_id)"
    )

    op.execute(
        "CREATE INDEX ix_mock_ledger_reference ON mock_ledger_entries (reference)"
    )

    op.execute("""
    CREATE TABLE mock_transfers (
        id VARCHAR(26) PRIMARY KEY,
        reference VARCHAR(64) NOT NULL UNIQUE,
        amount NUMERIC(18, 4) NOT NULL,
        currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
        dest_bank_code VARCHAR(10) NOT NULL,
        dest_account_number VARCHAR(20) NOT NULL,
        dest_account_name VARCHAR(128),
        status mocktransferstatus NOT NULL DEFAULT 'pending',
        failure_reason TEXT,
        provider_ref VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    op.execute(
        "CREATE INDEX ix_mock_transfer_reference ON mock_transfers (reference)"
    )

    op.execute("""
    CREATE TABLE mock_webhook_outbox (
        id VARCHAR(26) PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        event_type VARCHAR(64) NOT NULL,
        payload_json TEXT NOT NULL,
        target_url TEXT,
        status mockwebhookstatus NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TIMESTAMPTZ,
        delivered_at TIMESTAMPTZ,
        error TEXT
    )
    """)

    op.execute(
        "CREATE INDEX ix_mock_outbox_status ON mock_webhook_outbox (status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mock_webhook_outbox")
    op.execute("DROP TABLE IF EXISTS mock_transfers")
    op.execute("DROP TABLE IF EXISTS mock_ledger_entries")
    op.execute("DROP TABLE IF EXISTS mock_virtual_accounts")

    op.execute("DROP TYPE IF EXISTS mockwebhookstatus")
    op.execute("DROP TYPE IF EXISTS mocktransferstatus")
    op.execute("DROP TYPE IF EXISTS mockentrytype")
    op.execute("DROP TYPE IF EXISTS mockaccountstatus")