"""
app/providers/mock_bank/schemas.py
Request/response schemas for the mock bank's own REST surface.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional
from datetime import datetime

from pydantic import BaseModel, Field


# ── Virtual Accounts ─────────────────────────────────────────────────────────

class CreateVirtualAccountRequest(BaseModel):
    customer_ref: str = Field(..., description="Your user_id or any stable identifier")
    account_name: str = Field(..., min_length=2, max_length=128)
    currency: str = Field("NGN", max_length=3)
    metadata: Optional[dict[str, Any]] = None


class VirtualAccountResponse(BaseModel):
    id: str
    account_number: str
    bank_code: str
    bank_name: str
    account_name: str
    customer_ref: str
    currency: str
    balance: Decimal
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Transfers ─────────────────────────────────────────────────────────────────

class InitiateTransferRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    bank_code: str
    account_number: str
    reference: str = Field(..., min_length=3, max_length=64)
    currency: str = Field("NGN", max_length=3)


class TransferResponse(BaseModel):
    id: str
    reference: str
    provider_ref: str
    amount: Decimal
    currency: str
    dest_bank_code: str
    dest_account_number: str
    dest_account_name: Optional[str]
    status: str
    failure_reason: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Collections ───────────────────────────────────────────────────────────────

class InitialiseCollectionRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    currency: str = Field("NGN", max_length=3)
    reference: str = Field(..., min_length=3, max_length=64)
    customer_name: str
    customer_email: Optional[str] = None
    channels: list[str] = Field(default_factory=lambda: ["bank_transfer"])
    default_channel: str = "bank_transfer"


class CollectionResponse(BaseModel):
    reference: str
    provider_ref: str
    amount: Decimal
    currency: str
    status: str
    checkout_url: Optional[str] = None
    virtual_account: Optional[dict[str, Any]] = None


# ── Ledger / Transactions ─────────────────────────────────────────────────────

class LedgerEntryResponse(BaseModel):
    id: str
    entry_type: str
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    reference: str
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class MockTransactionResponse(BaseModel):
    """Generic transaction state response for /mock-bank/transactions/{ref}."""
    reference: str
    provider_ref: str
    type: str            # "transfer" | "collection"
    amount: Decimal
    currency: str
    status: str
    failure_reason: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Account Enquiry ───────────────────────────────────────────────────────────

class AccountEnquiryResponse(BaseModel):
    account_number: str
    bank_code: str
    account_name: str


# ── Banks ─────────────────────────────────────────────────────────────────────

class BankResponse(BaseModel):
    code: str
    name: str


# ── Webhook outbox ────────────────────────────────────────────────────────────

class WebhookOutboxResponse(BaseModel):
    id: str
    event_type: str
    status: str
    attempts: int
    last_attempt_at: Optional[datetime]
    delivered_at: Optional[datetime]
    error: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Cards ─────────────────────────────────────────────────────────────────────

class TokeniseCardRequest(BaseModel):
    """Fake card details submitted for tokenisation."""
    card_number: str = Field(..., min_length=16, max_length=19, description="16-19 digit card number")
    expiry_month: str = Field(..., pattern=r"^\d{2}$", description="MM e.g. 08")
    expiry_year: str = Field(..., pattern=r"^\d{4}$", description="YYYY e.g. 2028")
    cvv: str = Field(..., min_length=3, max_length=4)
    cardholder_name: str = Field(..., min_length=2, max_length=128)
    customer_ref: str = Field(..., description="Your user_id or stable identifier")

    # Optional — if omitted the mock bank infers from card number
    card_type: Optional[str] = Field(default=None, pattern="^(visa|mastercard|verve)$")
    bank_name: Optional[str] = None


class CardTokenResponse(BaseModel):
    token: str
    card_number_masked: str
    card_type: str
    expiry_month: str
    expiry_year: str
    cardholder_name: str
    bank_name: Optional[str]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CardChargeRequest(BaseModel):
    """Charge a tokenised card directly (for card-funded top-up simulation)."""
    token: str
    amount: Decimal = Field(..., gt=0)
    currency: str = Field("NGN", max_length=3)
    reference: str = Field(..., min_length=3, max_length=64)


class CardChargeResponse(BaseModel):
    reference: str
    provider_ref: str
    token: str
    amount: Decimal
    currency: str
    status: str
    failure_reason: Optional[str] = None