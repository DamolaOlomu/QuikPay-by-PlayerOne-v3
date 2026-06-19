"""add mock_cards table

Revision ID: 0004_mock_cards
Revises: 0003_mock_bank
Create Date: 2026-06-19

"""
from __future__ import annotations
from alembic import op

revision = "0004_mock_cards"
down_revision = "0003_mock_bank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE mockcardstatus AS ENUM ('active', 'expired', 'revoked')")
    op.execute("""
        CREATE TABLE mock_cards (
            id VARCHAR(26) PRIMARY KEY,
            token VARCHAR(64) NOT NULL UNIQUE,
            customer_ref VARCHAR(64) NOT NULL,
            card_number_masked VARCHAR(19) NOT NULL,
            card_type VARCHAR(16) NOT NULL DEFAULT 'visa',
            expiry_month VARCHAR(2) NOT NULL,
            expiry_year VARCHAR(4) NOT NULL,
            cardholder_name VARCHAR(128) NOT NULL,
            bank_name VARCHAR(64),
            status mockcardstatus NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ix_mock_card_token ON mock_cards (token)")
    op.execute("CREATE INDEX ix_mock_card_customer_ref ON mock_cards (customer_ref)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mock_cards")
    op.execute("DROP TYPE IF EXISTS mockcardstatus")