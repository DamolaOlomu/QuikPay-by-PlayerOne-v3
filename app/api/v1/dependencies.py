"""
app/api/v1/dependencies.py
FastAPI dependency functions — auth, DB, pagination, environment enforcement.

Changes from v3
───────────────
- get_current_user now accepts BOTH JWT bearer tokens AND API keys (p1t_ / p1l_)
- New: get_api_key_context — resolves the ApiKey row and enforces env isolation
- New: require_live_environment — gates live-only endpoints
- New: bind_user_to_request — writes user_id to request.state for the log middleware
- Existing: get_current_admin, pagination_params, get_idempotency_key — unchanged
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, AuthorizationError, InvalidTokenError
from app.core.security import decode_token, hash_api_key
from app.db.session import get_db
from app.models.api_key import ApiKey, KeyEnvironment, KeyStatus
from app.models.user import User, UserRole, UserStatus
from app.schemas.common import PaginationParams

bearer_scheme = HTTPBearer(auto_error=False)


# ── Internal helper ───────────────────────────────────────────────────────────

async def _user_from_id(user_id: str, db: AsyncSession) -> User:
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AuthenticationError("User not found.")
    if user.status == UserStatus.SUSPENDED:
        raise AuthenticationError("Account is suspended.")
    if user.status == UserStatus.CLOSED:
        raise AuthenticationError("Account is closed.")
    return user


async def _resolve_api_key(raw_token: str, db: AsyncSession) -> tuple[User, ApiKey]:
    """
    Look up an API key by its SHA-256 hash.
    Returns (User, ApiKey) or raises AuthenticationError.
    """
    from datetime import datetime, timezone
    from app.core.security import hash_api_key

    key_hash = hash_api_key(raw_token)
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.status == KeyStatus.ACTIVE,
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise AuthenticationError("Invalid or revoked API key.")

    # Check expiry
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise AuthenticationError("API key has expired.")

    user = await _user_from_id(api_key.user_id, db)
    return user, api_key


# ── Primary auth dependency ───────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Resolves either a JWT bearer token or a QuikPay API key → User.

    API keys:
      p1t_<48 hex>  — test environment
      p1l_<48 hex>  — live environment

    JWT tokens begin with "eyJ" (base64url).

    The resolved user is written to request.state.current_user_id so the
    RequestLogMiddleware can pick it up without re-decoding the token.
    """
    if not credentials:
        raise AuthenticationError("Authentication required.")

    token = credentials.credentials

    # ── API key path ──────────────────────────────────────────────────────────
    if token.startswith(("p1t_", "p1l_")):
        user, api_key = await _resolve_api_key(token, db)

        # Stash environment on request state for downstream env enforcement
        request.state.api_key_env = api_key.environment
        request.state.current_user_id = user.id
        request.state.api_key_id = api_key.id
        return user

    # ── JWT path ──────────────────────────────────────────────────────────────
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        if not user_id or token_type != "access":
            raise InvalidTokenError()
    except JWTError:
        raise InvalidTokenError("Invalid or expired token.")

    user = await _user_from_id(user_id, db)

    # JWT logins come from the dashboard — treat as LIVE context
    request.state.api_key_env = KeyEnvironment.LIVE
    request.state.current_user_id = user.id
    request.state.api_key_id = None
    return user


# ── Environment enforcement ───────────────────────────────────────────────────

async def get_request_environment(request: Request) -> KeyEnvironment:
    """
    Returns the environment associated with the current request's credentials.
    Must be used AFTER get_current_user has run (it sets request.state.api_key_env).
    """
    env = getattr(request.state, "api_key_env", None)
    if env is None:
        raise AuthenticationError("Authentication required.")
    return env


async def require_live_environment(
    env: KeyEnvironment = Depends(get_request_environment),
) -> None:
    """
    Dependency that blocks test API keys from hitting live-only endpoints.
    Add to any endpoint that must not be reachable with a test key:

        @router.post("/payouts", dependencies=[Depends(require_live_environment)])
    """
    if env == KeyEnvironment.TEST:
        raise AuthorizationError(
            "This endpoint requires a live API key. "
            "Test keys (p1t_) can only access sandbox resources."
        )


async def require_test_environment(
    env: KeyEnvironment = Depends(get_request_environment),
) -> None:
    """
    Inverse — prevents live keys from hitting sandbox-only mock endpoints.
    Used on the /mock-bank router.
    """
    if env == KeyEnvironment.LIVE:
        raise AuthorizationError(
            "This endpoint is only available in the test environment. "
            "Use a test API key (p1t_) to access mock bank resources."
        )


# ── Existing dependencies (unchanged) ────────────────────────────────────────

async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise AuthenticationError("Admin access required.")
    return current_user


def pagination_params(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> PaginationParams:
    return PaginationParams(page=page, per_page=per_page)


def get_idempotency_key(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Optional[str]:
    return idempotency_key


def get_request_id(
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> Optional[str]:
    return x_request_id