"""
app/services/api_key_service.py

Business logic for API key management.

Key generation
──────────────
  Test key:  p1t_<48 hex chars>   (prefix stored: first 8 chars e.g. "p1t_a3f9")
  Live key:  p1l_<48 hex chars>

The raw key is returned ONCE. Only the SHA-256 hash is stored.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.models.api_key import ApiKey, KeyEnvironment, KeyStatus
from app.schemas.developer import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse, ApiKeyRevokeResponse


def _generate_raw_key(environment: KeyEnvironment) -> str:
    prefix = "p1t" if environment == KeyEnvironment.TEST else "p1l"
    return f"{prefix}_{secrets.token_hex(24)}"


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


class ApiKeyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_key(self, user_id: str, payload: ApiKeyCreate) -> ApiKeyCreatedResponse:
        raw_key = _generate_raw_key(payload.environment)
        key_hash = _hash_key(raw_key)
        prefix = raw_key[:8]  # e.g. "p1t_a3f9"

        api_key = ApiKey(
            user_id=user_id,
            name=payload.name,
            prefix=prefix,
            key_hash=key_hash,
            environment=payload.environment,
            expires_at=payload.expires_at,
        )
        self.db.add(api_key)
        await self.db.flush()  # populate id/created_at

        return ApiKeyCreatedResponse(
            **ApiKeyResponse.model_validate(api_key).model_dump(),
            raw_key=raw_key,
        )

    async def list_keys(self, user_id: str) -> list[ApiKeyResponse]:
        result = await self.db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user_id, ApiKey.status != KeyStatus.REVOKED)
            .order_by(ApiKey.created_at.desc())
        )
        keys = result.scalars().all()
        return [ApiKeyResponse.model_validate(k) for k in keys]

    async def revoke_key(self, user_id: str, key_id: str) -> ApiKeyRevokeResponse:
        result = await self.db.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
        )
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise ResourceNotFoundError("API key not found.")
        if api_key.status == KeyStatus.REVOKED:
            raise AuthorizationError("API key is already revoked.")

        api_key.status = KeyStatus.REVOKED
        api_key.revoked_at = datetime.now(timezone.utc)

        return ApiKeyRevokeResponse(
            id=api_key.id,
            status=api_key.status,
            revoked_at=api_key.revoked_at,
        )

    async def get_key(self, user_id: str, key_id: str) -> ApiKeyResponse:
        result = await self.db.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
        )
        api_key = result.scalar_one_or_none()
        if not api_key:
            raise ResourceNotFoundError("API key not found.")
        return ApiKeyResponse.model_validate(api_key)

    # ── Used by auth dependency ───────────────────────────────────────────────

    @staticmethod
    def hash_raw_key(raw_key: str) -> str:
        return _hash_key(raw_key)