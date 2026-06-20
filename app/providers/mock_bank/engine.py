"""
app/providers/mock_bank/engine.py
Core mock bank engine.

Responsibilities:
  • Provision virtual accounts with deterministic account numbers
  • Manage internal ledger (double-entry, append-only)
  • Simulate transfers and collections via trigger rules
  • Write webhook events to the transactional outbox (same DB tx as ledger)
  • Generate bank-standard references and transaction IDs
"""
from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateResourceError, ResourceNotFoundError
from app.core.logging import get_logger
from app.providers.mock_bank.models import (
    MockCard,
    MockCardStatus,
    MockEntryType,
    MockLedgerEntry,
    MockTransfer,
    MockTransferStatus,
    MockVirtualAccount,
    MockWebhookOutbox,
)
from app.providers.mock_bank.triggers import (
    SimulatedOutcome,
    failure_reason_for,
    outcome_for_collection,
    outcome_for_transfer,
)

log = get_logger(__name__)

_MOCK_BANK_CODE = "999"
_MOCK_BANK_NAME = "MockBank MFB"

# Seed NIP directory: bank_code → bank_name
MOCK_NIP_DIRECTORY: dict[str, str] = {
    "011": "First Bank",
    "044": "Access Bank",
    "050": "EcoBank",
    "057": "Zenith Bank",
    "058": "GTBank",
    "063": "Diamond Bank",
    "076": "Polaris Bank",
    "232": "Sterling Bank",
    "301": "Jaiz Bank",
    "999": "MockBank MFB",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_account_number() -> str:
    """Generate a 10-digit mock NUBAN."""
    return "".join(secrets.choice(string.digits) for _ in range(10))


def _gen_provider_ref(prefix: str = "MB") -> str:
    return f"{prefix}{secrets.token_hex(8).upper()}"


class MockBankEngine:
    """
    Stateful engine backed by the application's SQLAlchemy session.
    Each method is a unit of work — the caller is responsible for commit/rollback.
    """

    def __init__(self, db: AsyncSession, webhook_url: Optional[str] = None) -> None:
        self.db = db
        self.webhook_url = webhook_url  # where to dispatch outbox events

    # ── Virtual Accounts ──────────────────────────────────────────────────────

    async def create_virtual_account(
        self,
        *,
        customer_ref: str,
        account_name: str,
        currency: str = "NGN",
        metadata: Optional[dict[str, Any]] = None,
    ) -> MockVirtualAccount:
        # Idempotent — same customer_ref → same account
        existing = await self.db.execute(
            select(MockVirtualAccount).where(
                MockVirtualAccount.customer_ref == customer_ref,
                MockVirtualAccount.currency == currency,
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            log.info("mock_bank.va.already_exists", customer_ref=customer_ref)
            return row

        account_number = await self._unique_account_number()
        va = MockVirtualAccount(
            account_number=account_number,
            bank_code=_MOCK_BANK_CODE,
            bank_name=_MOCK_BANK_NAME,
            account_name=account_name,
            customer_ref=customer_ref,
            currency=currency,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        self.db.add(va)
        await self.db.flush()
        log.info("mock_bank.va.created", account_number=account_number, customer_ref=customer_ref)
        return va

    async def _unique_account_number(self) -> str:
        for _ in range(10):
            candidate = _gen_account_number()
            result = await self.db.execute(
                select(MockVirtualAccount).where(MockVirtualAccount.account_number == candidate)
            )
            if not result.scalar_one_or_none():
                return candidate
        raise RuntimeError("Could not generate unique account number after 10 attempts")

    async def get_virtual_account(self, account_number: str) -> MockVirtualAccount:
        result = await self.db.execute(
            select(MockVirtualAccount).where(MockVirtualAccount.account_number == account_number)
        )
        va = result.scalar_one_or_none()
        if not va:
            raise ResourceNotFoundError(f"Virtual account {account_number!r} not found")
        return va

    async def list_virtual_accounts(self, customer_ref: str) -> list[MockVirtualAccount]:
        result = await self.db.execute(
            select(MockVirtualAccount).where(MockVirtualAccount.customer_ref == customer_ref)
        )
        return list(result.scalars().all())

    # ── Ledger ────────────────────────────────────────────────────────────────

    async def credit_account(
        self,
        *,
        account_number: str,
        amount: Decimal,
        reference: str,
        description: Optional[str] = None,
    ) -> MockLedgerEntry:
        va = await self.get_virtual_account(account_number)
        bal_before = va.balance
        va.balance = bal_before + amount
        entry = MockLedgerEntry(
            account_id=va.id,
            entry_type=MockEntryType.CREDIT,
            amount=amount,
            balance_before=bal_before,
            balance_after=va.balance,
            reference=reference,
            description=description,
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def debit_account(
        self,
        *,
        account_number: str,
        amount: Decimal,
        reference: str,
        description: Optional[str] = None,
    ) -> MockLedgerEntry:
        va = await self.get_virtual_account(account_number)
        if va.balance < amount:
            from app.core.exceptions import InsufficientFundsError
            raise InsufficientFundsError()
        bal_before = va.balance
        va.balance = bal_before - amount
        entry = MockLedgerEntry(
            account_id=va.id,
            entry_type=MockEntryType.DEBIT,
            amount=amount,
            balance_before=bal_before,
            balance_after=va.balance,
            reference=reference,
            description=description,
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    # ── Transfers ─────────────────────────────────────────────────────────────

    async def initiate_transfer(
        self,
        *,
        amount: Decimal,
        bank_code: str,
        account_number: str,
        reference: str,
        currency: str = "NGN",
    ) -> MockTransfer:
        # Idempotency check
        existing = await self.db.execute(
            select(MockTransfer).where(MockTransfer.reference == reference)
        )
        if existing.scalar_one_or_none():
            raise DuplicateResourceError(f"Transfer reference {reference!r} already exists")

        outcome = outcome_for_transfer(amount, reference)
        status_map = {
            SimulatedOutcome.SUCCESS: MockTransferStatus.SUCCESS,
            SimulatedOutcome.FAILED: MockTransferStatus.FAILED,
            SimulatedOutcome.PENDING: MockTransferStatus.PENDING,
        }

        provider_ref = _gen_provider_ref("MBT")
        dest_name = self._resolve_account_name(bank_code, account_number)

        transfer = MockTransfer(
            reference=reference,
            amount=amount,
            currency=currency,
            dest_bank_code=bank_code,
            dest_account_number=account_number,
            dest_account_name=dest_name,
            status=status_map[outcome],
            failure_reason=failure_reason_for(outcome, reference),
            provider_ref=provider_ref,
        )
        self.db.add(transfer)
        await self.db.flush()

        # Queue webhook event
        await self._enqueue_webhook(
            event_type=f"transfer.{outcome.value}",
            payload={
                "event": f"transfer.{outcome.value}",
                "reference": reference,
                "provider_ref": provider_ref,
                "amount": str(amount),
                "currency": currency,
                "status": outcome.value,
                "dest_bank_code": bank_code,
                "dest_account_number": account_number,
                "dest_account_name": dest_name,
                "failure_reason": transfer.failure_reason,
                "timestamp": _now().isoformat(),
            },
        )
        log.info(
            "mock_bank.transfer.initiated",
            reference=reference,
            outcome=outcome.value,
            amount=str(amount),
        )
        return transfer

    async def get_transfer(self, reference: str) -> MockTransfer:
        result = await self.db.execute(
            select(MockTransfer).where(MockTransfer.reference == reference)
        )
        t = result.scalar_one_or_none()
        if not t:
            raise ResourceNotFoundError(f"Transfer {reference!r} not found")
        return t

    # ── Collections ───────────────────────────────────────────────────────────

    async def initialise_collection(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference: str,
        customer_name: str,
        customer_email: Optional[str],
        channels: list[str],
        default_channel: str,
    ) -> dict[str, Any]:
        """
        Simulate a checkout session.  Returns a Glyde-compatible payload so the
        existing TransactionService webhook handler works unchanged.
        """
        outcome = outcome_for_collection(amount, reference)
        provider_ref = _gen_provider_ref("MBC")

        # If bank_transfer channel → vend a virtual account
        va_info: Optional[dict[str, Any]] = None
        if "bank_transfer" in channels:
            va_acct_no = _gen_account_number()
            va_info = {
                "account_number": va_acct_no,
                "bank_code": _MOCK_BANK_CODE,
                "bank_name": _MOCK_BANK_NAME,
                "account_name": customer_name,
            }

        checkout_url = f"https://mock.bank/pay/{provider_ref}"

        await self._enqueue_webhook(
            event_type=f"collection.{outcome.value}",
            payload={
                "event": f"collection.{outcome.value}",
                "reference": reference,
                "provider_ref": provider_ref,
                "amount": str(amount),
                "currency": currency,
                "status": outcome.value,
                "customer_name": customer_name,
                "customer_email": customer_email,
                "timestamp": _now().isoformat(),
            },
        )

        return {
            "reference": reference,
            "provider_ref": provider_ref,
            "amount": amount,
            "currency": currency,
            "status": "pending" if outcome == SimulatedOutcome.PENDING else outcome.value,
            "checkout_url": checkout_url,
            "virtual_account": va_info,
        }

    # ── Account Enquiry ───────────────────────────────────────────────────────

    def account_enquiry(self, *, account_number: str, bank_code: str) -> dict[str, Any]:
        """Resolve account → name. Magic prefix 0000 → not found."""
        if account_number.startswith("0000"):
            raise ResourceNotFoundError("Account not found in mock NIP directory")
        bank_name = MOCK_NIP_DIRECTORY.get(bank_code, "Unknown Bank")
        # Deterministic mock name from account number digits
        name = f"MOCK CUSTOMER {account_number[-4:]}"
        return {
            "account_number": account_number,
            "bank_code": bank_code,
            "bank_name": bank_name,
            "account_name": name,
        }

    def _resolve_account_name(self, bank_code: str, account_number: str) -> str:
        try:
            return self.account_enquiry(
                account_number=account_number, bank_code=bank_code
            )["account_name"]
        except Exception:
            return f"MOCK CUSTOMER {account_number[-4:]}"

    # ── Outbox ────────────────────────────────────────────────────────────────

    async def _enqueue_webhook(
        self, *, event_type: str, payload: dict[str, Any]
    ) -> MockWebhookOutbox:
        event = MockWebhookOutbox(
            event_type=event_type,
            payload_json=json.dumps(payload),
            target_url=self.webhook_url,
            status="pending",  # type: ignore[arg-type]
        )
        self.db.add(event)
        await self.db.flush()
        log.info("mock_bank.webhook.enqueued", event_type=event_type)
        return event

    async def pending_webhook_events(self) -> list[MockWebhookOutbox]:
        result = await self.db.execute(
            select(MockWebhookOutbox).where(MockWebhookOutbox.status == "pending")
        )
        return list(result.scalars().all())

    # ── Balance ───────────────────────────────────────────────────────────────

    async def float_balance(self) -> Decimal:
        """Aggregate balance across all mock virtual accounts (the 'float')."""
        result = await self.db.execute(select(MockVirtualAccount))
        accounts = result.scalars().all()
        return sum((a.balance for a in accounts), Decimal("0"))

    # ── Banks directory ───────────────────────────────────────────────────────

    def list_banks(self) -> list[dict[str, str]]:
        return [{"code": code, "name": name} for code, name in MOCK_NIP_DIRECTORY.items()]

    # ── Cards ─────────────────────────────────────────────────────────────────

    async def tokenise_card(
        self,
        *,
        card_number: str,
        expiry_month: str,
        expiry_year: str,
        cvv: str,
        cardholder_name: str,
        customer_ref: str,
        card_type: Optional[str] = None,
        bank_name: Optional[str] = None,
    ) -> MockCard:
        # Infer card type from first digit if not provided
        if not card_type:
            first = card_number.lstrip()[0]
            if first == "4":
                card_type = "visa"
            elif first == "5":
                card_type = "mastercard"
            elif first in ("5", "6"):
                card_type = "verve"
            else:
                card_type = "visa"

        # Mask card number: 4111 **** **** 1111
        digits = card_number.replace(" ", "").replace("-", "")
        masked = f"{digits[:4]} {'*' * 4} {'*' * 4} {digits[-4:]}"

        # Generate token
        token = f"mock_tok_{secrets.token_hex(16)}"

        card = MockCard(
            token=token,
            customer_ref=customer_ref,
            card_number_masked=masked,
            card_type=card_type,
            expiry_month=expiry_month,
            expiry_year=expiry_year,
            cardholder_name=cardholder_name.upper(),
            bank_name=bank_name or "MockBank MFB",
            status=MockCardStatus.ACTIVE,
        )
        self.db.add(card)
        await self.db.flush()
        log.info("mock_bank.card.tokenised", token=token, customer_ref=customer_ref)
        return card

    async def get_card(self, token: str) -> MockCard:
        result = await self.db.execute(
            select(MockCard).where(MockCard.token == token)
        )
        card = result.scalar_one_or_none()
        if not card:
            raise ResourceNotFoundError(f"Card token {token!r} not found")
        return card

    async def list_cards(self, customer_ref: str) -> list[MockCard]:
        result = await self.db.execute(
            select(MockCard).where(MockCard.customer_ref == customer_ref)
        )
        return list(result.scalars().all())

    async def charge_card(
        self,
        *,
        token: str,
        amount: Decimal,
        currency: str,
        reference: str,
    ) -> dict:
        """
        Simulate a card charge. Uses the same trigger rules as transfers.
        Returns a Glyde-compatible charge response dict.
        """
        card = await self.get_card(token)
        if card.status != MockCardStatus.ACTIVE:
            raise ResourceNotFoundError(f"Card {token!r} is {card.status.value}")

        outcome = outcome_for_transfer(amount, reference)
        provider_ref = _gen_provider_ref("MBC")
        failure_reason = None

        if outcome == SimulatedOutcome.FAILED:
            failure_reason = "Card declined by mock bank"

        await self._enqueue_webhook(
            event_type=f"card.charge.{outcome.value}",
            payload={
                "event": f"card.charge.{outcome.value}",
                "reference": reference,
                "provider_ref": provider_ref,
                "token": token,
                "amount": str(amount),
                "currency": currency,
                "status": outcome.value,
                "card_type": card.card_type,
                "card_last4": card.card_number_masked[-4:],
                "failure_reason": failure_reason,
                "timestamp": _now().isoformat(),
            },
        )

        log.info("mock_bank.card.charged", token=token, outcome=outcome.value, amount=str(amount))
        return {
            "reference": reference,
            "provider_ref": provider_ref,
            "token": token,
            "amount": amount,
            "currency": currency,
            "status": outcome.value,
            "failure_reason": failure_reason,
        }