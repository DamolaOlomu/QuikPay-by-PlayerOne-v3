"""
app/api/v1/endpoints/transactions.py
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user, get_idempotency_key
from app.db.session import get_db
from app.models.transaction import TransactionStatus
from app.models.user import User
from app.schemas.common import APIResponse, PaginatedResponse
from app.schemas.transaction import (
    AccountEnquiryResponse,
    BankTransferRequest,
    CardPaymentRequest,
    TopUpBankTransferRequest,
    TopUpCardRequest,
    TopUpVirtualAccountRequest,
    TransactionResponse,
    TransactionStatusUpdate,
    VirtualAccountRequest,
    VirtualAccountResponse,
    WalletFundRequest,
    WalletFundResponse,
    WalletResponse,
    WalletSendRequest,
)
from app.services.transaction_service import TransactionService

router = APIRouter(prefix="/transactions", tags=["Transactions"])


# ── Wallet ────────────────────────────────────────────────────────────────────

@router.get(
    "/wallet",
    response_model=APIResponse[WalletResponse],
    summary="Get wallet balance",
)
async def get_wallet(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    data = await svc.get_wallet(current_user.id)
    return APIResponse(data=WalletResponse(**data))


# ── Top-up wallet ─────────────────────────────────────────────────────────────

@router.post(
    "/wallet/top-up/card",
    response_model=APIResponse[WalletFundResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Top up wallet via card payment",
    description=(
        "Charge a tokenised card to fund the wallet. "
        "Get a card token first from POST /mock-bank/cards/tokenise."
    ),
)
async def top_up_via_card(
    payload: TopUpCardRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    # Build a WalletFundRequest internally
    fund_payload = WalletFundRequest(
        amount=payload.amount,
        currency=payload.currency,
        method="card",
        description=payload.description,
        metadata={**(payload.metadata or {}), "card_token": payload.card_token},
    )
    svc = TransactionService(db)
    txn = await svc.fund_wallet(fund_payload, current_user.id, idempotency_key or payload.idempotency_key)
    await db.commit()

    instructions = None
    if txn.metadata_json:
        meta = json.loads(txn.metadata_json)
        glyde = meta.get("glyde_response", {}).get("data", {})
        if glyde.get("checkout_url"):
            instructions = {"checkout_url": glyde["checkout_url"]}

    return APIResponse(
        data=WalletFundResponse(
            wallet_id=current_user.wallet_id,
            method="card",
            transaction=TransactionResponse.model_validate(txn),
            funding_instructions=instructions,
        ),
        message="Card payment initiated.",
    )


@router.post(
    "/wallet/top-up/bank-transfer",
    response_model=APIResponse[WalletFundResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Top up wallet via direct bank transfer",
    description="Returns bank account details to transfer into. Wallet is credited on confirmation.",
)
async def top_up_via_bank_transfer(
    payload: TopUpBankTransferRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    fund_payload = WalletFundRequest(
        amount=payload.amount,
        currency=payload.currency,
        method="bank_transfer",
        bank_transfer_option="glyde_bank_account",
        description=payload.description,
        metadata=payload.metadata,
    )
    svc = TransactionService(db)
    txn = await svc.fund_wallet(fund_payload, current_user.id, idempotency_key or payload.idempotency_key)
    await db.commit()

    instructions = None
    if txn.metadata_json:
        meta = json.loads(txn.metadata_json)
        glyde = meta.get("glyde_response", {}).get("data", {})
        if glyde:
            instructions = {
                "account_number": glyde.get("account_number"),
                "account_name": glyde.get("account_name"),
                "bank_name": glyde.get("bank_name"),
                "bank_code": glyde.get("bank_code"),
                "amount": str(payload.amount),
                "reference": txn.reference,
            }

    return APIResponse(
        data=WalletFundResponse(
            wallet_id=current_user.wallet_id,
            method="bank_transfer",
            bank_transfer_option="glyde_bank_account",
            transaction=TransactionResponse.model_validate(txn),
            funding_instructions=instructions,
        ),
        message="Transfer the exact amount to the bank details provided.",
    )


@router.post(
    "/wallet/top-up/virtual-account",
    response_model=APIResponse[WalletFundResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Top up wallet via virtual account",
    description="Generates a dedicated virtual account. Any transfer into it credits your wallet.",
)
async def top_up_via_virtual_account(
    payload: TopUpVirtualAccountRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    fund_payload = WalletFundRequest(
        amount=payload.amount,
        currency=payload.currency,
        method="bank_transfer",
        bank_transfer_option="virtual_account",
        description=payload.description,
        metadata=payload.metadata,
    )
    svc = TransactionService(db)
    txn = await svc.fund_wallet(fund_payload, current_user.id, idempotency_key or payload.idempotency_key)
    await db.commit()

    instructions = None
    if txn.metadata_json:
        meta = json.loads(txn.metadata_json)
        glyde = meta.get("glyde_response", {}).get("data", {})
        if glyde:
            instructions = {
                "account_number": glyde.get("account_number"),
                "account_name": glyde.get("account_name"),
                "bank_name": glyde.get("bank_name"),
                "bank_code": glyde.get("bank_code"),
                "note": "Transfer any amount to this account to fund your wallet.",
            }

    return APIResponse(
        data=WalletFundResponse(
            wallet_id=current_user.wallet_id,
            method="bank_transfer",
            bank_transfer_option="virtual_account",
            transaction=TransactionResponse.model_validate(txn),
            funding_instructions=instructions,
        ),
        message="Virtual account generated. Transfer funds to credit your wallet.",
    )


# ── Send money out ────────────────────────────────────────────────────────────

@router.post(
    "/send/wallet",
    response_model=APIResponse[TransactionResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Send money to another QuikPay wallet",
    description="Instant internal transfer. Settles immediately.",
)
async def send_to_wallet(
    payload: WalletSendRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    txn = await svc.send_to_wallet(payload, current_user.id, idempotency_key)
    await db.commit()
    return APIResponse(
        data=TransactionResponse.model_validate(txn),
        message="Transfer successful.",
    )


@router.post(
    "/send/bank-transfer",
    response_model=APIResponse[TransactionResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Send money to a bank account",
    description="Debits wallet and initiates an outbound NIP transfer to the destination account.",
)
async def send_bank_transfer(
    payload: BankTransferRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    txn = await svc.create_bank_transfer(payload, current_user.id, idempotency_key)
    await db.commit()
    return APIResponse(
        data=TransactionResponse.model_validate(txn),
        message="Bank transfer initiated.",
    )


@router.post(
    "/send/virtual-account",
    response_model=APIResponse[VirtualAccountResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate a virtual account for a recipient",
    description="Generates a VA the recipient can receive funds into.",
)
async def send_via_virtual_account(
    payload: VirtualAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    data = await svc.generate_virtual_account(payload, current_user.id)
    return APIResponse(
        data=VirtualAccountResponse(**data),
        message="Virtual account generated.",
    )


@router.post(
    "/send/card",
    response_model=APIResponse[TransactionResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Make an external card payment",
    description="Pay a merchant or external party using a tokenised card. Funds deducted from wallet.",
)
async def send_via_card(
    payload: CardPaymentRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(get_idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    txn = await svc.create_card_payment(payload, current_user.id, idempotency_key)
    await db.commit()
    return APIResponse(
        data=TransactionResponse.model_validate(txn),
        message="Card payment initiated.",
    )


# ── Utilities ─────────────────────────────────────────────────────────────────

@router.get(
    "/banks",
    response_model=APIResponse[list],
    summary="List supported banks",
)
async def list_banks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    data = await svc.list_banks()
    return APIResponse(data=data.get("data", []))


@router.get(
    "/account-enquiry",
    response_model=APIResponse[AccountEnquiryResponse],
    summary="Resolve account number to account name",
)
async def account_enquiry(
    account_number: str = Query(..., min_length=10, max_length=10),
    bank_code: str = Query(..., min_length=2, max_length=10),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    data = await svc.resolve_account_name(account_number, bank_code)
    return APIResponse(data=AccountEnquiryResponse(**data))


@router.get(
    "/virtual-account",
    response_model=APIResponse[VirtualAccountResponse],
    summary="Get or generate the user's dedicated virtual account",
)
async def get_virtual_account(
    type: str = Query(default="dynamic", pattern="^(dynamic|static)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    data = await svc.generate_virtual_account(
        VirtualAccountRequest(type=type),
        current_user.id,
    )
    return APIResponse(data=VirtualAccountResponse(**data))


# ── History ───────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=PaginatedResponse[TransactionResponse],
    summary="List current user's transactions",
)
async def list_transactions(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    status_filter: Optional[TransactionStatus] = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    txns, total = await svc.list_transactions(
        user_id=current_user.id,
        page=page,
        per_page=per_page,
        status=status_filter,
    )
    return PaginatedResponse(
        data=[TransactionResponse.model_validate(t) for t in txns],
        total=total,
        page=page,
        per_page=per_page,
        has_next=(page * per_page) < total,
    )


@router.get(
    "/wallet",
    response_model=APIResponse[WalletResponse],
    summary="Get wallet balance",
    include_in_schema=False,  # already defined above, avoid duplicate
)
async def get_wallet_alias(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    data = await svc.get_wallet(current_user.id)
    return APIResponse(data=WalletResponse(**data))


@router.get(
    "/{transaction_id}",
    response_model=APIResponse[TransactionResponse],
    summary="Get a single transaction",
)
async def get_transaction(
    transaction_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    txn = await svc.get_transaction(transaction_id, user_id=current_user.id)
    return APIResponse(data=TransactionResponse.model_validate(txn))


@router.patch(
    "/{transaction_id}/status",
    response_model=APIResponse[TransactionResponse],
    summary="Update transaction status (state machine enforced)",
)
async def update_transaction_status(
    transaction_id: str,
    payload: TransactionStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TransactionService(db)
    txn = await svc.update_status(transaction_id, payload, actor_id=current_user.id)
    await db.commit()
    return APIResponse(
        data=TransactionResponse.model_validate(txn),
        message=f"Transaction status updated to {txn.status.value}.",
    )