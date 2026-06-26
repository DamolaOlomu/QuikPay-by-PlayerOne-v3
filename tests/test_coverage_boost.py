"""
tests/test_coverage_boost.py
Targeted tests to push coverage from 65% → 70%+.
Covers: admin endpoints, user service edge cases, api key rotation,
        soft-delete, suspended account login, token errors, dependencies.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole, UserStatus

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_admin(db: AsyncSession, phone: str = "+2349000000001") -> tuple[User, str]:
    """Directly insert an admin user and return (user, password)."""
    from app.core.security import hash_password
    admin = User(
        phone_number=phone,
        fullname="Admin User",
        email="admin@test.com",
        hashed_password=hash_password("AdminPass123!"),
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        currency="NGN",
    )
    db.add(admin)
    await db.flush()
    return admin, "AdminPass123!"


async def _admin_token(client: AsyncClient, db: AsyncSession) -> str:
    await _make_admin(db)
    resp = await client.post("/api/v1/users/login", json={
        "phone_number": "+2349000000001",
        "password": "AdminPass123!",
    })
    return resp.json()["data"]["access_token"]


# ── Admin endpoint ────────────────────────────────────────────────────────────

class TestAdminEndpoints:

    async def test_create_admin_success(self, client: AsyncClient, db: AsyncSession):
        token = await _admin_token(client, db)
        resp = await client.post(
            "/api/v1/users/admin",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "phone_number": "+2349111111111",
                "fullname": "New Admin",
                "email": "newadmin@test.com",
                "password": "AdminPass456!",
                "currency": "NGN",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["role"] == "admin"
        assert data["fullname"] == "New Admin"

    async def test_create_admin_requires_admin_token(self, client: AsyncClient, auth_headers: dict):
        # Regular user cannot create admins
        resp = await client.post(
            "/api/v1/users/admin",
            headers=auth_headers,
            json={
                "phone_number": "+2349222222222",
                "fullname": "Sneaky Admin",
                "email": "sneaky@test.com",
                "password": "AdminPass789!",
                "currency": "NGN",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "authentication_error"

    async def test_create_admin_duplicate_phone(self, client: AsyncClient, db: AsyncSession):
        token = await _admin_token(client, db)
        payload = {
            "phone_number": "+2349333333333",
            "fullname": "Dup Admin",
            "email": "dup@test.com",
            "password": "AdminPass123!",
            "currency": "NGN",
        }
        await client.post("/api/v1/users/admin", headers={"Authorization": f"Bearer {token}"}, json=payload)
        resp = await client.post("/api/v1/users/admin", headers={"Authorization": f"Bearer {token}"}, json=payload)
        assert resp.status_code == 409

    async def test_create_admin_duplicate_email(self, client: AsyncClient, db: AsyncSession):
        token = await _admin_token(client, db)
        await client.post("/api/v1/users/admin", headers={"Authorization": f"Bearer {token}"}, json={
            "phone_number": "+2349444444441",
            "fullname": "Admin One",
            "email": "shared@test.com",
            "password": "AdminPass123!",
            "currency": "NGN",
        })
        resp = await client.post("/api/v1/users/admin", headers={"Authorization": f"Bearer {token}"}, json={
            "phone_number": "+2349444444442",
            "fullname": "Admin Two",
            "email": "shared@test.com",
            "password": "AdminPass123!",
            "currency": "NGN",
        })
        assert resp.status_code == 409

    async def test_admin_get_user_by_id(self, client: AsyncClient, db: AsyncSession, registered_user: dict):
        token = await _admin_token(client, db)
        user_id = registered_user["id"]
        resp = await client.get(f"/api/v1/users/{user_id}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == user_id

    async def test_admin_get_user_not_found(self, client: AsyncClient, db: AsyncSession):
        token = await _admin_token(client, db)
        resp = await client.get("/api/v1/users/01NONEXISTENT0000000000000", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

    async def test_admin_delete_user(self, client: AsyncClient, db: AsyncSession, registered_user: dict):
        token = await _admin_token(client, db)
        user_id = registered_user["id"]
        resp = await client.delete(f"/api/v1/users/{user_id}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "User deactivated."

    async def test_non_admin_cannot_get_user_by_id(self, client: AsyncClient, auth_headers: dict, registered_user: dict):
        resp = await client.get(f"/api/v1/users/{registered_user['id']}", headers=auth_headers)
        assert resp.status_code == 401


# ── API Key tests ─────────────────────────────────────────────────────────────

class TestApiKey:

    async def test_rotate_api_key_returns_raw_key(self, client: AsyncClient, auth_headers: dict):
        resp = await client.post(
            "/api/v1/developer/keys",
            json={"name": "test key", "environment": "test"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "raw_key" in data
        assert len(data["raw_key"]) > 20

    async def test_rotate_api_key_twice_returns_different_keys(self, client: AsyncClient, auth_headers: dict):
        r1 = await client.post(
            "/api/v1/developer/keys",
            json={"name": "key one", "environment": "test"},
            headers=auth_headers,
        )
        r2 = await client.post(
            "/api/v1/developer/keys",
            json={"name": "key two", "environment": "test"},
            headers=auth_headers,
        )
        assert r1.json()["data"]["raw_key"] != r2.json()["data"]["raw_key"]


# ── Suspended account ─────────────────────────────────────────────────────────

class TestSuspendedAccount:

    async def test_suspended_user_cannot_login(self, client: AsyncClient, db: AsyncSession):
        # Register user
        await client.post("/api/v1/users/register", json={
            "phone_number": "+2349555555555",
            "fullname": "Suspended User",
            "password": "SecurePass123!",
            "currency": "NGN",
        })
        # Suspend them directly
        result = await db.execute(select(User).where(User.phone_number == "+2349555555555"))
        user = result.scalar_one()
        user.status = UserStatus.SUSPENDED
        await db.flush()

        resp = await client.post("/api/v1/users/login", json={
            "phone_number": "+2349555555555",
            "password": "SecurePass123!",
        })
        assert resp.status_code == 401
        assert "suspended" in resp.json()["message"].lower()

    async def test_suspended_user_cannot_access_protected_routes(
        self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession
    ):
        # Suspend the registered user after getting their token
        result = await db.execute(select(User).where(User.id == registered_user["id"]))
        user = result.scalar_one()
        user.status = UserStatus.SUSPENDED
        await db.flush()

        resp = await client.get("/api/v1/users/me", headers=auth_headers)
        assert resp.status_code == 401


# ── Token edge cases ──────────────────────────────────────────────────────────

class TestTokenEdgeCases:

    async def test_refresh_with_access_token_fails(self, client: AsyncClient, auth_headers: dict):
        # Extract access token and try to use it as a refresh token
        access_token = auth_headers["Authorization"].split(" ")[1]
        resp = await client.post("/api/v1/users/refresh", json={"refresh_token": access_token})
        assert resp.status_code == 401

    async def test_refresh_with_garbage_token_fails(self, client: AsyncClient):
        resp = await client.post("/api/v1/users/refresh", json={"refresh_token": "not.a.real.token"})
        assert resp.status_code == 401

    async def test_closed_account_cannot_access_protected_routes(
        self, client: AsyncClient, auth_headers: dict, registered_user: dict, db: AsyncSession
    ):
        result = await db.execute(select(User).where(User.id == registered_user["id"]))
        user = result.scalar_one()
        user.status = UserStatus.CLOSED
        await db.flush()

        resp = await client.get("/api/v1/users/me", headers=auth_headers)
        assert resp.status_code == 401


# ── User service direct ───────────────────────────────────────────────────────

class TestUserServiceDirect:

    async def test_delete_user_soft_deletes(self, client: AsyncClient, db: AsyncSession, registered_user: dict):
        from app.services.user_service import UserService
        svc = UserService(db)
        await svc.delete_user(registered_user["id"], actor_id="admin-id")
        # User should now be soft deleted
        result = await db.execute(select(User).where(User.id == registered_user["id"]))
        user = result.scalar_one()
        assert user.is_deleted is True

    async def test_get_user_not_found_raises(self, client: AsyncClient, db: AsyncSession):
        from app.core.exceptions import UserNotFoundError
        from app.services.user_service import UserService
        svc = UserService(db)
        with pytest.raises(UserNotFoundError):
            await svc.get_user("01NONEXISTENT0000000000000")

    async def test_update_user_fields(self, client: AsyncClient, db: AsyncSession, registered_user: dict):
        from app.services.user_service import UserService
        from app.schemas.user import UserUpdate
        svc = UserService(db)
        updated = await svc.update_user(registered_user["id"], UserUpdate(fullname="Updated Name"))
        assert updated.fullname == "Updated Name"