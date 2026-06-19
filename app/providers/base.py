"""
app/providers/base.py
Protocol (interface) that every payment rail must satisfy.
Add methods here as new capabilities are needed; adapters implement them.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class PaymentProviderClient(Protocol):
    """Minimal contract every payment rail must satisfy."""

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def banks(self) -> dict[str, Any]:
        """Return list of supported banks."""
        ...

    async def account_enquiry(self, *, account_number: str, bank_code: str) -> dict[str, Any]:
        """Resolve account number → account name."""
        ...

    async def balance(self) -> dict[str, Any]:
        """Return the provider float/settlement balance."""
        ...

    # ── Disbursement ──────────────────────────────────────────────────────────

    async def initiate_transfer(
        self,
        *,
        amount: Decimal,
        bank_code: str,
        account_number: str,
        reference: str,
    ) -> dict[str, Any]:
        """Initiate an outbound bank transfer."""
        ...

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
        """Initialise a checkout / collection session."""
        ...

    async def collection_bank_transfer(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference: str,
        customer_name: str,
        customer_email: Optional[str],
    ) -> dict[str, Any]:
        """Create a bank-transfer collection (NIP/NEFT inbound)."""
        ...

    # ── Virtual accounts ──────────────────────────────────────────────────────

    async def create_virtual_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Provision a dedicated virtual account number for a customer."""
        ...

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def fetch_transaction(self, reference: str) -> dict[str, Any]:
        """Fetch the current state of a transaction by provider reference."""
        ...
