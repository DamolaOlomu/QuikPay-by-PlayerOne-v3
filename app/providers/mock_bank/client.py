"""
app/providers/mock_bank/client.py
Adapter that wraps MockBankEngine behind the PaymentProviderClient protocol.
This is what the factory returns when PAYMENT_PROVIDER=mock.

Since the engine requires a DB session (it writes ledger rows), the client
creates a short-lived session for each call using the app's session factory.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from app.core.logging import get_logger
from app.providers.mock_bank.engine import MockBankEngine
from app.providers.mock_bank.models import MockTransferStatus

log = get_logger(__name__)


class MockBankClient:
    """Thin async adapter — satisfies PaymentProviderClient without inheriting it."""

    async def _engine(self) -> tuple[MockBankEngine, Any]:
        """Return (engine, session). Caller must commit/close the session."""
        from app.db.session import AsyncSessionLocal
        session = AsyncSessionLocal()
        engine = MockBankEngine(session)
        return engine, session

    # ── Banks ─────────────────────────────────────────────────────────────────

    async def banks(self) -> dict[str, Any]:
        engine, session = await self._engine()
        async with session:
            result = engine.list_banks()
            return {"status": "success", "data": result}

    # ── Account enquiry ───────────────────────────────────────────────────────

    async def account_enquiry(self, *, account_number: str, bank_code: str) -> dict[str, Any]:
        engine, session = await self._engine()
        async with session:
            data = engine.account_enquiry(account_number=account_number, bank_code=bank_code)
            return {"status": "success", "data": data}

    # ── Balance ───────────────────────────────────────────────────────────────

    async def balance(self) -> dict[str, Any]:
        engine, session = await self._engine()
        async with session:
            bal = await engine.float_balance()
            return {"status": "success", "data": {"balance": str(bal), "currency": "NGN"}}

    # ── Transfer ──────────────────────────────────────────────────────────────

    async def initiate_transfer(
        self,
        *,
        amount: Decimal,
        bank_code: str,
        account_number: str,
        reference: str,
    ) -> dict[str, Any]:
        engine, session = await self._engine()
        async with session.begin():
            transfer = await engine.initiate_transfer(
                amount=amount,
                bank_code=bank_code,
                account_number=account_number,
                reference=reference,
            )
        status_map = {
            MockTransferStatus.SUCCESS: "success",
            MockTransferStatus.FAILED: "failed",
            MockTransferStatus.PENDING: "pending",
        }
        return {
            "status": "success",
            "data": {
                "reference": transfer.reference,
                "provider_ref": transfer.provider_ref,
                "amount": str(transfer.amount),
                "status": status_map[transfer.status],
                "failure_reason": transfer.failure_reason,
            },
        }

    # ── Collection ────────────────────────────────────────────────────────────

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
        engine, session = await self._engine()
        async with session.begin():
            result = await engine.initialise_collection(
                amount=amount,
                currency=currency,
                reference=reference,
                customer_name=customer_name,
                customer_email=customer_email,
                channels=channels,
                default_channel=default_channel,
            )
        return {"status": "success", "data": result}

    async def collection_bank_transfer(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference: str,
        customer_name: str,
        customer_email: Optional[str],
    ) -> dict[str, Any]:
        return await self.initialise_collection(
            amount=amount,
            currency=currency,
            reference=reference,
            customer_name=customer_name,
            customer_email=customer_email,
            channels=["bank_transfer"],
            default_channel="bank_transfer",
        )

    # ── Virtual accounts ──────────────────────────────────────────────────────

    async def create_virtual_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        engine, session = await self._engine()
        async with session.begin():
            va = await engine.create_virtual_account(
                customer_ref=payload.get("customer_ref", payload.get("reference", "unknown")),
                account_name=payload.get(
                    "account_name",
                    payload.get("customer", {}).get("name", "Mock Customer"),
                ),
                currency=payload.get("currency", "NGN"),
                metadata=payload.get("metadata"),
            )
        return {
            "status": "success",
            "data": {
                "id": va.id,
                "account_number": va.account_number,
                "bank_code": va.bank_code,
                "bank_name": va.bank_name,
                "account_name": va.account_name,
                "customer_ref": va.customer_ref,
                "currency": va.currency,
                "balance": str(va.balance),
                "status": va.status.value,
            },
        }

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def fetch_transaction(self, reference: str) -> dict[str, Any]:
        engine, session = await self._engine()
        async with session:
            transfer = await engine.get_transfer(reference)
        return {
            "status": "success",
            "data": {
                "reference": transfer.reference,
                "provider_ref": transfer.provider_ref,
                "type": "transfer",
                "amount": str(transfer.amount),
                "currency": transfer.currency,
                "status": transfer.status.value,
                "failure_reason": transfer.failure_reason,
            },
        }
