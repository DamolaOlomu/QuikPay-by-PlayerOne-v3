"""
tests/test_mock_bank_and_service.py
Targets uncovered lines in:
  - app/providers/mock_bank/triggers.py  (33% → ~80%)
  - app/providers/mock_bank/engine.py    (28% → ~50%)
  - app/services/transaction_service.py  (41% → ~55%)
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User, UserStatus
from app.providers.mock_bank.engine import MockBankEngine
from app.providers.mock_bank.triggers import (
    SimulatedOutcome,
    failure_reason_for,
    outcome_for_collection,
    outcome_for_transfer,
)
from app.schemas.transaction import (
    BankTransferRequest,
    VirtualAccountRequest,
    WalletFundRequest,
    WalletSendRequest,
)
from app.services.transaction_service import TransactionService

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _fund_user(db: AsyncSession, user_id: str, amount: float = 100_000.0):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.balance = amount
    user.status = UserStatus.ACTIVE
    await db.flush()


# ── triggers.py ───────────────────────────────────────────────────────────────

class TestTriggers:

    def test_amount_00_always_success(self):
        assert outcome_for_transfer(Decimal("100.00"), "REF1") == SimulatedOutcome.SUCCESS

    def test_amount_01_always_failed(self):
        assert outcome_for_transfer(Decimal("100.01"), "REF2") == SimulatedOutcome.FAILED

    def test_amount_02_always_pending(self):
        assert outcome_for_transfer(Decimal("100.02"), "REF3") == SimulatedOutcome.PENDING

    def test_fail_prefix_forces_failed(self):
        assert outcome_for_transfer(Decimal("100.00"), "FAIL_REF") == SimulatedOutcome.FAILED

    def test_pend_prefix_forces_pending(self):
        assert outcome_for_transfer(Decimal("100.00"), "PEND_REF") == SimulatedOutcome.PENDING

    def test_outcome_for_collection_delegates_to_transfer(self):
        assert outcome_for_collection(Decimal("100.00"), "REF") == SimulatedOutcome.SUCCESS
        assert outcome_for_collection(Decimal("100.01"), "REF") == SimulatedOutcome.FAILED

    def test_failure_reason_none_for_success(self):
        assert failure_reason_for(SimulatedOutcome.SUCCESS, "REF") is None

    def test_failure_reason_none_for_pending(self):
        assert failure_reason_for(SimulatedOutcome.PENDING, "REF") is None

    def test_failure_reason_insuf(self):
        reason = failure_reason_for(SimulatedOutcome.FAILED, "INSUF_REF")
        assert "Insufficient" in reason

    def test_failure_reason_invalid(self):
        reason = failure_reason_for(SimulatedOutcome.FAILED, "INVALID_REF")
        assert "Invalid" in reason

    def test_failure_reason_generic(self):
        reason = failure_reason_for(SimulatedOutcome.FAILED, "SOME_OTHER_REF")
        assert "declined" in reason.lower()


# ── engine.py ─────────────────────────────────────────────────────────────────

class TestMockBankEngine:

    async def test_create_virtual_account(self, db: AsyncSession):
        engine = MockBankEngine(db)
        va = await engine.create_virtual_account(
            customer_ref="WLT-TEST-001",
            account_name="Test User",
            currency="NGN",
        )
        assert len(va.account_number) == 10
        assert va.bank_code == "999"
        assert va.currency == "NGN"

    async def test_create_virtual_account_idempotent(self, db: AsyncSession):
        engine = MockBankEngine(db)
        va1 = await engine.create_virtual_account(customer_ref="WLT-IDEM-001", account_name="User", currency="NGN")
        va2 = await engine.create_virtual_account(customer_ref="WLT-IDEM-001", account_name="User", currency="NGN")
        assert va1.account_number == va2.account_number

    async def test_get_virtual_account(self, db: AsyncSession):
        engine = MockBankEngine(db)
        va = await engine.create_virtual_account(customer_ref="WLT-GET-001", account_name="User", currency="NGN")
        fetched = await engine.get_virtual_account(va.account_number)
        assert fetched.id == va.id

    async def test_get_virtual_account_not_found(self, db: AsyncSession):
        from app.core.exceptions import ResourceNotFoundError
        engine = MockBankEngine(db)
        with pytest.raises(ResourceNotFoundError):
            await engine.get_virtual_account("9999999999")

    async def test_list_virtual_accounts(self, db: AsyncSession):
        engine = MockBankEngine(db)
        await engine.create_virtual_account(customer_ref="WLT-LIST-001", account_name="User", currency="NGN")
        await engine.create_virtual_account(customer_ref="WLT-LIST-001", account_name="User", currency="USD")
        accounts = await engine.list_virtual_accounts("WLT-LIST-001")
        assert len(accounts) == 2

    async def test_credit_account(self, db: AsyncSession):
        engine = MockBankEngine(db)
        va = await engine.create_virtual_account(customer_ref="WLT-CR-001", account_name="User", currency="NGN")
        entry = await engine.credit_account(
            account_number=va.account_number,
            amount=Decimal("5000.00"),
            reference="CR-REF-001",
        )
        assert entry.amount == Decimal("5000.00")
        updated = await engine.get_virtual_account(va.account_number)
        assert updated.balance == Decimal("5000.00")

    async def test_debit_account(self, db: AsyncSession):
        engine = MockBankEngine(db)
        va = await engine.create_virtual_account(customer_ref="WLT-DB-001", account_name="User", currency="NGN")
        await engine.credit_account(account_number=va.account_number, amount=Decimal("10000"), reference="SEED")
        entry = await engine.debit_account(
            account_number=va.account_number,
            amount=Decimal("3000.00"),
            reference="DB-REF-001",
        )
        assert entry.amount == Decimal("3000.00")

    async def test_debit_insufficient_funds(self, db: AsyncSession):
        from app.core.exceptions import InsufficientFundsError
        engine = MockBankEngine(db)
        va = await engine.create_virtual_account(customer_ref="WLT-INSUF-001", account_name="User", currency="NGN")
        with pytest.raises(InsufficientFundsError):
            await engine.debit_account(account_number=va.account_number, amount=Decimal("9999"), reference="OVER")

    async def test_initiate_transfer_success(self, db: AsyncSession):
        engine = MockBankEngine(db)
        transfer = await engine.initiate_transfer(
            amount=Decimal("1000.00"),  # .00 = success
            bank_code="044",
            account_number="0123456789",
            reference="TRF-SUCCESS-001",
            currency="NGN",
        )
        from app.providers.mock_bank.models import MockTransferStatus
        assert transfer.status == MockTransferStatus.SUCCESS

    async def test_initiate_transfer_failed(self, db: AsyncSession):
        engine = MockBankEngine(db)
        transfer = await engine.initiate_transfer(
            amount=Decimal("1000.01"),  # .01 = failed
            bank_code="044",
            account_number="0123456789",
            reference="TRF-FAIL-001",
            currency="NGN",
        )
        from app.providers.mock_bank.models import MockTransferStatus
        assert transfer.status == MockTransferStatus.FAILED
        assert transfer.failure_reason is not None

    async def test_get_transfer(self, db: AsyncSession):
        engine = MockBankEngine(db)
        await engine.initiate_transfer(
            amount=Decimal("500.00"),
            bank_code="058",
            account_number="1234567890",
            reference="TRF-GET-001",
        )
        t = await engine.get_transfer("TRF-GET-001")
        assert t.reference == "TRF-GET-001"

    async def test_get_transfer_not_found(self, db: AsyncSession):
        from app.core.exceptions import ResourceNotFoundError
        engine = MockBankEngine(db)
        with pytest.raises(ResourceNotFoundError):
            await engine.get_transfer("NONEXISTENT-REF")

    async def test_initialise_collection(self, db: AsyncSession):
        engine = MockBankEngine(db)
        result = await engine.initialise_collection(
            amount=Decimal("2000.00"),
            currency="NGN",
            reference="COL-001",
            customer_name="Test User",
            customer_email="test@example.com",
            channels=["bank_transfer"],
            default_channel="bank_transfer",
        )
        assert "checkout_url" in result
        assert result["virtual_account"] is not None

    async def test_account_enquiry_success(self, db: AsyncSession):
        engine = MockBankEngine(db)
        result = engine.account_enquiry(account_number="1234567890", bank_code="044")
        assert result["bank_name"] == "Access Bank"
        assert "account_name" in result

    async def test_account_enquiry_not_found(self, db: AsyncSession):
        from app.core.exceptions import ResourceNotFoundError
        engine = MockBankEngine(db)
        with pytest.raises(ResourceNotFoundError):
            engine.account_enquiry(account_number="0000000000", bank_code="044")

    async def test_float_balance(self, db: AsyncSession):
        engine = MockBankEngine(db)
        balance = await engine.float_balance()
        assert balance >= Decimal("0")

    async def test_list_banks(self, db: AsyncSession):
        engine = MockBankEngine(db)
        banks = engine.list_banks()
        assert len(banks) > 0
        assert all("code" in b and "name" in b for b in banks)

    async def test_tokenise_and_charge_card(self, db: AsyncSession):
        engine = MockBankEngine(db)
        card = await engine.tokenise_card(
            card_number="4111111111111111",
            expiry_month="12",
            expiry_year="2027",
            cvv="123",
            cardholder_name="Test User",
            customer_ref="WLT-CARD-001",
        )
        assert card.token.startswith("mock_tok_")
        assert card.card_type == "visa"

        result = await engine.charge_card(
            token=card.token,
            amount=Decimal("1000.00"),  # .00 = success
            currency="NGN",
            reference="CHG-001",
        )
        assert result["status"] == "success"

    async def test_get_card_not_found(self, db: AsyncSession):
        from app.core.exceptions import ResourceNotFoundError
        engine = MockBankEngine(db)
        with pytest.raises(ResourceNotFoundError):
            await engine.get_card("mock_tok_nonexistent")

    async def test_list_cards(self, db: AsyncSession):
        engine = MockBankEngine(db)
        await engine.tokenise_card(
            card_number="5500000000000004",
            expiry_month="01",
            expiry_year="2028",
            cvv="456",
            cardholder_name="Card User",
            customer_ref="WLT-CARDS-001",
        )
        cards = await engine.list_cards("WLT-CARDS-001")
        assert len(cards) == 1
        assert cards[0].card_type == "mastercard"

    async def test_pending_webhook_events(self, db: AsyncSession):
        engine = MockBankEngine(db)
        # Trigger a transfer to enqueue a webhook
        await engine.initiate_transfer(
            amount=Decimal("100.00"),
            bank_code="044",
            account_number="0123456789",
            reference="WH-001",
        )
        events = await engine.pending_webhook_events()
        assert len(events) >= 1


# ── transaction_service.py ────────────────────────────────────────────────────

class TestTransactionServiceDirect:

    async def test_fund_wallet_bank_transfer(self, db: AsyncSession, registered_user: dict):
        await _fund_user(db, registered_user["id"])
        svc = TransactionService(db)
        txn = await svc.fund_wallet(
            WalletFundRequest(amount=Decimal("5000.00"), method="bank_transfer"),
            registered_user["id"],
            idempotency_key="fund-bt-001",
        )
        assert txn.transaction_type.value == "fund_wallet"

    async def test_fund_wallet_virtual_account(self, db: AsyncSession, registered_user: dict):
        svc = TransactionService(db)
        txn = await svc.fund_wallet(
            WalletFundRequest(
                amount=Decimal("3000.00"),
                method="bank_transfer",
                bank_transfer_option="virtual_account",
            ),
            registered_user["id"],
            idempotency_key="fund-va-001",
        )
        assert txn.transaction_type.value == "fund_wallet"

    async def test_fund_wallet_idempotency(self, db: AsyncSession, registered_user: dict):
        svc = TransactionService(db)
        t1 = await svc.fund_wallet(
            WalletFundRequest(amount=Decimal("1000.00"), method="bank_transfer"),
            registered_user["id"],
            idempotency_key="fund-idem-001",
        )
        t2 = await svc.fund_wallet(
            WalletFundRequest(amount=Decimal("1000.00"), method="bank_transfer"),
            registered_user["id"],
            idempotency_key="fund-idem-001",
        )
        assert t1.id == t2.id

    async def test_generate_virtual_account_mock(self, db: AsyncSession, registered_user: dict):
        svc = TransactionService(db)
        result = await svc.generate_virtual_account(
            VirtualAccountRequest(preferred_bank_code="999"),
            registered_user["id"],
        )
        assert result["wallet_id"] == registered_user["wallet_id"]
        assert len(result["account_number"]) == 10

    async def test_list_banks_mock(self, db: AsyncSession):
        svc = TransactionService(db)
        result = await svc.list_banks()
        assert "data" in result
        assert len(result["data"]) > 0

    async def test_resolve_account_name_mock(self, db: AsyncSession):
        svc = TransactionService(db)
        result = await svc.resolve_account_name("1234567890", "044")
        assert "account_name" in result

    async def test_get_wallet(self, db: AsyncSession, registered_user: dict):
        svc = TransactionService(db)
        wallet = await svc.get_wallet(registered_user["id"])
        assert wallet["wallet_id"] == registered_user["wallet_id"]
        assert "balance" in wallet

    async def test_send_to_wallet_insufficient_funds(self, db: AsyncSession, registered_user: dict):
        from app.core.exceptions import InsufficientFundsError
        # Register recipient
        from app.core.security import hash_password
        recipient = User(
            phone_number="+2349888888881",
            fullname="Recipient",
            hashed_password=hash_password("Pass123!"),
            currency="NGN",
            status=UserStatus.ACTIVE,
        )
        db.add(recipient)
        await db.flush()

        svc = TransactionService(db)
        with pytest.raises(InsufficientFundsError):
            await svc.send_to_wallet(
                WalletSendRequest(
                    recipient_wallet_id=recipient.wallet_id,
                    amount=Decimal("999999.00"),
                    currency="NGN",
                ),
                registered_user["id"],
            )

    async def test_create_bank_transfer_insufficient_funds(self, db: AsyncSession, registered_user: dict):
        from app.core.exceptions import InsufficientFundsError
        svc = TransactionService(db)
        with pytest.raises(InsufficientFundsError):
            await svc.create_bank_transfer(
                BankTransferRequest(
                    amount=Decimal("999999.00"),
                    bank_code="044",
                    account_number="0123456789",
                    account_name="Jane Doe",
                ),
                registered_user["id"],
            )