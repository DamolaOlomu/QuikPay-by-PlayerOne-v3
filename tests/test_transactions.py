# tests/test_transactions.py
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserStatus

pytestmark = pytest.mark.asyncio


async def _fund_user(db: AsyncSession, user_id: str, amount: float = 100_000.0):
    """Helper: directly credit a user's balance for test setup."""
    from sqlalchemy import select
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.balance = amount
    user.status = UserStatus.ACTIVE
    await db.flush()


class TestCreateTransaction:

    async def test_create_deposit(self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession):
        await _fund_user(db, registered_user["id"])

        # Actual route: POST /api/v1/transactions/wallet/top-up/bank-transfer
        resp = await client.post(
            "/api/v1/transactions/wallet/top-up/bank-transfer",
            headers=auth_headers,
            json={
                "amount": "5000.00",
                "currency": "NGN",
                "description": "Test deposit",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        txn = data["transaction"]
        assert txn["status"] == "initiated"
        assert txn["transaction_type"] == "fund_wallet"
        assert float(txn["amount"]) == 5000.0
        assert txn["fee"] is not None
        assert txn["reference"].startswith("P1P")

    async def test_create_insufficient_funds(self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession):
        # User has 0 balance — send money should fail
        resp = await client.post(
            "/api/v1/transactions/send/wallet",
            headers=auth_headers,
            json={
                "recipient_wallet_id": "WLT-FAKE-001",
                "amount": "999999.00",
                "currency": "NGN",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "insufficient_funds"

    async def test_idempotency_same_key_returns_same_transaction(
        self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession
    ):
        await _fund_user(db, registered_user["id"])
        headers = {**auth_headers, "Idempotency-Key": "test-idem-key-001"}
        payload = {
            "amount": "1000.00",
            "currency": "NGN",
        }

        r1 = await client.post("/api/v1/transactions/wallet/top-up/bank-transfer", headers=headers, json=payload)
        r2 = await client.post("/api/v1/transactions/wallet/top-up/bank-transfer", headers=headers, json=payload)

        assert r1.status_code == 201
        assert r2.status_code == 201
        # Same transaction returned both times
        assert r1.json()["data"]["transaction"]["id"] == r2.json()["data"]["transaction"]["id"]
        assert r1.json()["data"]["transaction"]["reference"] == r2.json()["data"]["transaction"]["reference"]


class TestGetTransaction:

    async def test_get_own_transaction(self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession):
        await _fund_user(db, registered_user["id"])
        create_resp = await client.post(
            "/api/v1/transactions/wallet/top-up/bank-transfer",
            headers=auth_headers,
            json={"amount": "500.00", "currency": "NGN"},
        )
        txn_id = create_resp.json()["data"]["transaction"]["id"]

        resp = await client.get(f"/api/v1/transactions/{txn_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == txn_id
        assert "events" in resp.json()["data"]

    async def test_cannot_get_other_users_transaction(self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession):
        await _fund_user(db, registered_user["id"])
        create_resp = await client.post(
            "/api/v1/transactions/wallet/top-up/bank-transfer",
            headers=auth_headers,
            json={"amount": "500.00", "currency": "NGN"},
        )
        txn_id = create_resp.json()["data"]["transaction"]["id"]

        # Register a second user
        await client.post("/api/v1/users/register", json={
            "phone_number": "+2348099998888",
            "fullname": "Other User",
            "password": "Password123!",
        })
        login2 = await client.post("/api/v1/users/login", json={
            "phone_number": "+2348099998888",
            "password": "Password123!",
        })
        headers2 = {"Authorization": f"Bearer {login2.json()['data']['access_token']}"}

        resp = await client.get(f"/api/v1/transactions/{txn_id}", headers=headers2)
        assert resp.status_code == 404


class TestStatusTransition:

    async def test_valid_transition_initiated_to_pending(
        self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession
    ):
        await _fund_user(db, registered_user["id"])
        create_resp = await client.post(
            "/api/v1/transactions/wallet/top-up/bank-transfer",
            headers=auth_headers,
            json={"amount": "1000.00", "currency": "NGN"},
        )
        txn_id = create_resp.json()["data"]["transaction"]["id"]

        resp = await client.patch(
            f"/api/v1/transactions/{txn_id}/status",
            headers=auth_headers,
            json={"status": "pending", "note": "Processing started"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "pending"

    async def test_invalid_transition_rejected(
        self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession
    ):
        await _fund_user(db, registered_user["id"])
        create_resp = await client.post(
            "/api/v1/transactions/wallet/top-up/bank-transfer",
            headers=auth_headers,
            json={"amount": "1000.00", "currency": "NGN"},
        )
        txn_id = create_resp.json()["data"]["transaction"]["id"]

        # INITIATED → SUCCESS is not allowed (must go through PENDING → PROCESSING first)
        resp = await client.patch(
            f"/api/v1/transactions/{txn_id}/status",
            headers=auth_headers,
            json={"status": "success"},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "invalid_state_transition"

    async def test_full_lifecycle(
        self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession
    ):
        await _fund_user(db, registered_user["id"], amount=50_000)

        # Use send/wallet to test a debit lifecycle (send_money type)
        # First register a recipient
        await client.post("/api/v1/users/register", json={
            "phone_number": "+2348011112222",
            "fullname": "Recipient",
            "password": "SecurePass123!",
            "currency": "NGN",
        })
        recipient_resp = await client.post("/api/v1/users/login", json={
            "phone_number": "+2348011112222",
            "password": "SecurePass123!",
        })
        recipient_data = recipient_resp.json()["data"]

        # Get recipient wallet_id
        recipient_me = await client.get(
            "/api/v1/transactions/wallet",
            headers={"Authorization": f"Bearer {recipient_data['access_token']}"},
        )
        recipient_wallet_id = recipient_me.json()["data"]["wallet_id"]

        create_resp = await client.post(
            "/api/v1/transactions/send/wallet",
            headers=auth_headers,
            json={
                "recipient_wallet_id": recipient_wallet_id,
                "amount": "1000.00",
                "currency": "NGN",
            },
        )
        assert create_resp.status_code == 201
        txn = create_resp.json()["data"]
        # send/wallet settles immediately at "success"
        assert txn["status"] == "success"

        # Check balance was debited
        balance_resp = await client.get("/api/v1/users/me/balance", headers=auth_headers)
        balance = balance_resp.json()["data"]["balance"]
        assert balance < 50_000


class TestListTransactions:

    async def test_list_returns_paginated(self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession):
        await _fund_user(db, registered_user["id"])
        for i in range(3):
            await client.post(
                "/api/v1/transactions/wallet/top-up/bank-transfer",
                headers=auth_headers,
                json={"amount": str(100 + i), "currency": "NGN"},
            )

        # Actual route: GET /api/v1/transactions
        resp = await client.get("/api/v1/transactions?per_page=2", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["total"] >= 3
        assert body["has_next"] is True