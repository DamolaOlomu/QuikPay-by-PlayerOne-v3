"""
app/providers/mock_bank/router.py
Mock bank REST surface — mounted at /api/v1/mock-bank in dev/test environments.

Endpoints:
  GET  /mock-bank/banks
  GET  /mock-bank/account-enquiry
  GET  /mock-bank/balance

  POST /mock-bank/virtual-accounts
  GET  /mock-bank/virtual-accounts/{account_number}
  GET  /mock-bank/virtual-accounts                   (by ?customer_ref=)

  POST /mock-bank/transfers
  GET  /mock-bank/transfers/{reference}

  POST /mock-bank/collections
  GET  /mock-bank/transactions/{reference}

  GET  /mock-bank/webhook-outbox
  POST /mock-bank/webhook-outbox/dispatch            (manually drain outbox)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_db, require_test_environment
from app.core.logging import get_logger
from app.providers.mock_bank.engine import MockBankEngine
from app.providers.mock_bank.schemas import (
    CardChargeRequest,
    CardTokenResponse,
    TokeniseCardRequest,
    AccountEnquiryResponse,
    BankResponse,
    CollectionResponse,
    CreateVirtualAccountRequest,
    InitialiseCollectionRequest,
    InitiateTransferRequest,
    LedgerEntryResponse,
    MockTransactionResponse,
    TransferResponse,
    VirtualAccountResponse,
    WebhookOutboxResponse,
)
from app.schemas.common import APIResponse

log = get_logger(__name__)

router = APIRouter(
    prefix="/mock-bank",
    tags=["Mock Bank (Sandbox)"],
    dependencies=[Depends(require_test_environment)],
)


def _engine(db: AsyncSession = Depends(get_db)) -> MockBankEngine:
    return MockBankEngine(db)


# ── Banks ─────────────────────────────────────────────────────────────────────

@router.get("/banks", response_model=dict)
async def list_banks(engine: MockBankEngine = Depends(_engine)):
    """Return the mock NIP bank directory."""
    return {"success": True, "data": engine.list_banks()}


# ── Account enquiry ───────────────────────────────────────────────────────────

@router.get("/account-enquiry", response_model=dict)
async def account_enquiry(
    account_number: str = Query(...),
    bank_code: str = Query(...),
    engine: MockBankEngine = Depends(_engine),
):
    """Resolve account number → account name (mock NIP enquiry)."""
    data = engine.account_enquiry(account_number=account_number, bank_code=bank_code)
    return {"success": True, "data": data}


# ── Balance ───────────────────────────────────────────────────────────────────

@router.get("/balance", response_model=dict)
async def float_balance(engine: MockBankEngine = Depends(_engine)):
    """Return aggregate mock bank float balance."""
    bal = await engine.float_balance()
    return {"success": True, "data": {"balance": str(bal), "currency": "NGN"}}


# ── Virtual Accounts ──────────────────────────────────────────────────────────

@router.post("/virtual-accounts", response_model=dict, status_code=201)
async def create_virtual_account(
    body: CreateVirtualAccountRequest,
    db: AsyncSession = Depends(get_db),
    engine: MockBankEngine = Depends(_engine),
):
    va = await engine.create_virtual_account(
        customer_ref=body.customer_ref,
        account_name=body.account_name,
        currency=body.currency,
        metadata=body.metadata,
    )
    await db.commit()
    return {"success": True, "data": VirtualAccountResponse.model_validate(va).model_dump()}


@router.get("/virtual-accounts", response_model=dict)
async def list_virtual_accounts(
    customer_ref: str = Query(...),
    engine: MockBankEngine = Depends(_engine),
):
    accounts = await engine.list_virtual_accounts(customer_ref)
    return {
        "success": True,
        "data": [VirtualAccountResponse.model_validate(a).model_dump() for a in accounts],
    }


@router.get("/virtual-accounts/{account_number}", response_model=dict)
async def get_virtual_account(
    account_number: str,
    engine: MockBankEngine = Depends(_engine),
):
    va = await engine.get_virtual_account(account_number)
    return {"success": True, "data": VirtualAccountResponse.model_validate(va).model_dump()}


# ── Transfers ─────────────────────────────────────────────────────────────────

@router.post("/transfers", response_model=dict, status_code=201)
async def initiate_transfer(
    body: InitiateTransferRequest,
    db: AsyncSession = Depends(get_db),
    engine: MockBankEngine = Depends(_engine),
):
    """Simulate an outbound bank transfer. Outcome is determined by trigger rules."""
    transfer = await engine.initiate_transfer(
        amount=body.amount,
        bank_code=body.bank_code,
        account_number=body.account_number,
        reference=body.reference,
        currency=body.currency,
    )
    await db.commit()
    return {"success": True, "data": TransferResponse.model_validate(transfer).model_dump()}


@router.get("/transfers/{reference}", response_model=dict)
async def get_transfer(reference: str, engine: MockBankEngine = Depends(_engine)):
    transfer = await engine.get_transfer(reference)
    return {"success": True, "data": TransferResponse.model_validate(transfer).model_dump()}


# ── Collections ───────────────────────────────────────────────────────────────

@router.post("/collections", response_model=dict, status_code=201)
async def initialise_collection(
    body: InitialiseCollectionRequest,
    db: AsyncSession = Depends(get_db),
    engine: MockBankEngine = Depends(_engine),
):
    """Simulate a collection / checkout session."""
    result = await engine.initialise_collection(
        amount=body.amount,
        currency=body.currency,
        reference=body.reference,
        customer_name=body.customer_name,
        customer_email=body.customer_email,
        channels=body.channels,
        default_channel=body.default_channel,
    )
    await db.commit()
    return {"success": True, "data": result}


# ── Transactions (generic lookup) ─────────────────────────────────────────────

@router.get("/transactions/{reference}", response_model=dict)
async def fetch_transaction(reference: str, engine: MockBankEngine = Depends(_engine)):
    """Fetch a transfer by reference (Glyde-compatible lookup surface)."""
    transfer = await engine.get_transfer(reference)
    return {
        "success": True,
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


# ── Webhook outbox ────────────────────────────────────────────────────────────

@router.get("/webhook-outbox", response_model=dict)
async def list_outbox(engine: MockBankEngine = Depends(_engine)):
    """Inspect all enqueued webhook events (useful for debugging)."""
    events = await engine.pending_webhook_events()
    return {
        "success": True,
        "data": [WebhookOutboxResponse.model_validate(e).model_dump() for e in events],
    }


@router.post("/webhook-outbox/dispatch", response_model=dict)
async def dispatch_outbox(db: AsyncSession = Depends(get_db)):
    """Manually drain the webhook outbox (dev/test convenience endpoint)."""
    from app.providers.mock_bank.dispatcher import dispatch_pending
    count = await dispatch_pending(db)
    return {"success": True, "data": {"dispatched": count}}


# ── Cards ─────────────────────────────────────────────────────────────────────

@router.post("/cards/tokenise", response_model=dict, status_code=201)
async def tokenise_card(
    body: TokeniseCardRequest,
    db: AsyncSession = Depends(get_db),
    engine: MockBankEngine = Depends(_engine),
):
    """
    Submit fake card details → get a token back.
    Use this token in card payment/top-up endpoints.

    Test card numbers:
      4111 1111 1111 1111  → always SUCCESS (Visa)
      4000 0000 0000 0002  → always FAILED  (Visa decline)
      5500 0000 0000 0004  → always SUCCESS (Mastercard)
      5105 1051 0510 5100  → always PENDING (Mastercard)
    """
    card = await engine.tokenise_card(
        card_number=body.card_number,
        expiry_month=body.expiry_month,
        expiry_year=body.expiry_year,
        cvv=body.cvv,
        cardholder_name=body.cardholder_name,
        customer_ref=body.customer_ref,
        card_type=body.card_type,
        bank_name=body.bank_name,
    )
    await db.commit()
    from app.providers.mock_bank.schemas import CardTokenResponse
    return {"success": True, "data": CardTokenResponse.model_validate(card).model_dump()}


@router.get("/cards", response_model=dict)
async def list_cards(
    customer_ref: str = Query(...),
    engine: MockBankEngine = Depends(_engine),
):
    """List all tokenised cards for a customer."""
    from app.providers.mock_bank.schemas import CardTokenResponse
    cards = await engine.list_cards(customer_ref)
    return {
        "success": True,
        "data": [CardTokenResponse.model_validate(c).model_dump() for c in cards],
    }


@router.get("/cards/{token}", response_model=dict)
async def get_card(token: str, engine: MockBankEngine = Depends(_engine)):
    """Look up a card token."""
    from app.providers.mock_bank.schemas import CardTokenResponse
    card = await engine.get_card(token)
    return {"success": True, "data": CardTokenResponse.model_validate(card).model_dump()}


@router.post("/cards/{token}/charge", response_model=dict, status_code=201)
async def charge_card(
    token: str,
    body: CardChargeRequest,
    db: AsyncSession = Depends(get_db),
    engine: MockBankEngine = Depends(_engine),
):
    """
    Directly charge a tokenised card (simulates card-funded top-up).
    Outcome follows trigger rules — amount ending .01 → FAILED, .00 → SUCCESS.
    """
    result = await engine.charge_card(
        token=token,
        amount=body.amount,
        currency=body.currency,
        reference=body.reference,
    )
    await db.commit()
    return {"success": True, "data": result}