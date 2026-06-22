"""
app/middleware/request_logger.py

Writes a RequestLog row for every authenticated API request.
Uses a FastAPI BackgroundTask so the DB write happens AFTER the response is
sent — zero latency impact on the caller.

Only logs requests that carry authentication (Bearer token). Health checks,
docs, and unauthenticated 401s are not logged.

Environment tagging
───────────────────
If the Bearer token starts with "p1t_" → TEST environment.
If it starts with "p1l_" → LIVE environment.
JWT-authenticated requests (eyJ...) → LIVE by default (dashboard login).

API key resolution
──────────────────
We look up the ApiKey row by its prefix to get the api_key_id FK.
If no matching key is found (JWT auth) api_key_id is NULL.
"""
from __future__ import annotations

import time
from typing import Optional

from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger
from app.models.api_key import KeyEnvironment

log = get_logger(__name__)

_SKIP_PATHS = (
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/",
)


def _resolve_environment(token: str) -> KeyEnvironment:
    if token.startswith("p1t_"):
        return KeyEnvironment.TEST
    if token.startswith("p1l_"):
        return KeyEnvironment.LIVE
    return KeyEnvironment.LIVE  # JWT dashboard login = live context


async def _write_log(
    *,
    user_id: Optional[str],
    api_key_id: Optional[str],
    environment: KeyEnvironment,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    error_code: Optional[str],
    request_id: Optional[str],
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> None:
    """Runs in background after response is sent."""
    try:
        from app.db.session import AsyncSessionLocal
        from app.models.request_log import RequestLog

        async with AsyncSessionLocal() as session:
            log_entry = RequestLog(
                user_id=user_id,
                api_key_id=api_key_id,
                environment=environment,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                success=status_code < 400,
                error_code=error_code,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            session.add(log_entry)

            # Bump the ApiKey usage counters if we have a key
            if api_key_id:
                from sqlalchemy import select, update
                from datetime import datetime, timezone
                from app.models.api_key import ApiKey

                await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == api_key_id)
                    .values(
                        request_count=ApiKey.request_count + 1,
                        last_used_at=datetime.now(timezone.utc),
                    )
                )

            await session.commit()
    except Exception as exc:
        log.warning("request_logger.write_failed", exc_info=exc)


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    Register in main.py AFTER RateLimitMiddleware:
        app.add_middleware(RequestLogMiddleware)

    Must run inside RequestIDMiddleware so X-Request-ID is available.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip non-API paths
        if any(path.startswith(p) for p in _SKIP_PATHS):
            return await call_next(request)

        # Only log authenticated requests
        auth: Optional[str] = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            return await call_next(request)

        token = auth[7:]
        environment = _resolve_environment(token)

        # Resolve api_key_id from token prefix (non-blocking, best-effort)
        api_key_id: Optional[str] = None
        if token.startswith(("p1t_", "p1l_")):
            prefix = token[:8]
            request.state.api_key_prefix = prefix  # consumed by auth dependency
            try:
                from app.db.session import AsyncSessionLocal
                from app.models.api_key import ApiKey
                from sqlalchemy import select

                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(ApiKey.id).where(ApiKey.prefix == prefix)
                    )
                    row = result.scalar_one_or_none()
                    if row:
                        api_key_id = row
            except Exception:
                pass  # fail open

        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000)

        # Extract user_id from request state (set by auth dependency)
        user_id: Optional[str] = getattr(request.state, "current_user_id", None)
        request_id: Optional[str] = response.headers.get("X-Request-ID")

        # Error code from response body is not cheaply readable here;
        # we record None and let the log row status_code tell the story.
        error_code: Optional[str] = None

        ip_address: Optional[str] = None
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host

        user_agent = request.headers.get("User-Agent")

        response.background = BackgroundTask(
            _write_log,
            user_id=user_id,
            api_key_id=api_key_id,
            environment=environment,
            method=request.method,
            path=path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            error_code=error_code,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return response