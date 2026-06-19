"""
app/providers/mock_bank/models.py
Mock bank internal ledger — virtual accounts, ledger entries, outbox events.
These tables are created alongside the main app tables in dev/test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import (
    String, Numeric, Text, DateTime, Boolean,
    Enum as SAEnum, Index, ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, ULIDPrimaryKeyMixin


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MockAccountStatus(str, Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class MockEntryType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class MockWebhookStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class MockTransferStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class MockVirtualAccount(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    """A provisioned virtual bank account number in the mock bank."""
    __tablename__ = "mock_virtual_accounts"
    __table_args__ = (
        Index("ix_mock_va_account_number", "account_number"),
        Index("ix_mock_va_customer_ref", "customer_ref"),
    )

    account_number: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    bank_code: Mapped[str] = mapped_column(String(10), default="999", nullable=False)
    bank_name: Mapped[str] = mapped_column(String(64), default="MockBank MFB", nullable=False)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_ref: Mapped[str] = mapped_column(String(64), nullable=False)  # user_id or any ref
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=4), default=Decimal("0"), nullable=False
    )
    status: Mapped[MockAccountStatus] = mapped_column(
        SAEnum(MockAccountStatus, name="mockaccountstatus", values_callable=lambda x: [e.value for e in x]),
        default=MockAccountStatus.ACTIVE,
        nullable=False,
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    entries: Mapped[list["MockLedgerEntry"]] = relationship(
        back_populates="account", order_by="MockLedgerEntry.created_at"
    )


class MockLedgerEntry(ULIDPrimaryKeyMixin, Base):
    """Immutable double-entry ledger row. Never updated after insert."""
    __tablename__ = "mock_ledger_entries"
    __table_args__ = (
        Index("ix_mock_ledger_account_id", "account_id"),
        Index("ix_mock_ledger_reference", "reference"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("mock_virtual_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    entry_type: Mapped[MockEntryType] = mapped_column(
        SAEnum(MockEntryType, name="mockentrytype", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4), nullable=False)
    balance_before: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4), nullable=False)
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    account: Mapped["MockVirtualAccount"] = relationship(back_populates="entries")


class MockTransfer(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    """Record of every outbound transfer attempt through the mock bank."""
    __tablename__ = "mock_transfers"
    __table_args__ = (
        Index("ix_mock_transfer_reference", "reference"),
    )

    reference: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    dest_bank_code: Mapped[str] = mapped_column(String(10), nullable=False)
    dest_account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    dest_account_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[MockTransferStatus] = mapped_column(
        SAEnum(MockTransferStatus, name="mocktransferstatus", values_callable=lambda x: [e.value for e in x]),
        default=MockTransferStatus.PENDING,
        nullable=False,
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_ref: Mapped[str] = mapped_column(String(64), nullable=False)  # mock bank's own ref


class MockWebhookOutbox(ULIDPrimaryKeyMixin, Base):
    """
    Transactional outbox — webhook events written in the same DB transaction
    as the ledger change, then dispatched asynchronously.
    Prevents the race where the DB commits but the webhook fires before the
    caller's response returns.
    """
    __tablename__ = "mock_webhook_outbox"
    __table_args__ = (
        Index("ix_mock_outbox_status", "status"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)   # e.g. "transfer.success"
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)        # serialised WebhookEvent
    target_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[MockWebhookStatus] = mapped_column(
        SAEnum(MockWebhookStatus, name="mockwebhookstatus", values_callable=lambda x: [e.value for e in x]),
        default=MockWebhookStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class MockCardStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class MockCard(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A tokenised mock card. Stores fake card details behind a token.
    The token is what gets passed to card payment endpoints.
    """
    __tablename__ = "mock_cards"
    __table_args__ = (
        Index("ix_mock_card_token", "token"),
        Index("ix_mock_card_customer_ref", "customer_ref"),
    )

    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    customer_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    card_number_masked: Mapped[str] = mapped_column(String(19), nullable=False)  # e.g. 4111 **** **** 1111
    card_type: Mapped[str] = mapped_column(String(16), nullable=False, default="visa")  # visa | mastercard | verve
    expiry_month: Mapped[str] = mapped_column(String(2), nullable=False)
    expiry_year: Mapped[str] = mapped_column(String(4), nullable=False)
    cardholder_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bank_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[MockCardStatus] = mapped_column(
        SAEnum(MockCardStatus, name="mockcardstatus", values_callable=lambda x: [e.value for e in x]),
        default=MockCardStatus.ACTIVE,
        nullable=False,
    )