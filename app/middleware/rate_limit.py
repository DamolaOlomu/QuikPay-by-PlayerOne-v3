"""
app/middleware/rate_limit.py

Sliding-window rate limiter: 100 requests per minute per API key (or user_id
for JWT-authenticated requests without an API key).

Storage backend: Redis (production).
Fallback: in-process dict (dev / Redis unavailable) — NOT shared across workers,
so the effective limit per worker process is 100 req/min. Fine for local dev;
always use Redis in staging/prod.

How the sliding window works
────────────────────────────
  key = "rl:<identifier>"
  MULTI:
    ZREMRANGEBYSCORE key 0 (now_ms - 60_000)   # drop entries older than 1 min
    ZADD key now_ms now_ms                      # log this request
    ZCARD key                                   # count requests in window
    EXPIRE key 61                               # auto-clean
  EXEC

If ZCARD > RATE_LIMIT_PER_MINUTE → 429.

Exempt paths (never rate-limited):
  /health  /ready  /  /docs  /redoc  /openapi.json

Headers returned on every request:
  X-RateLimit-Limit:     100
  X-RateLimit-Remaining: N
  X-RateLimit-Reset:     Unix timestamp (next window reset, approximate)
  Retry-After:           seconds  (only on 429)
"""
from __future__ import annotations

import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
settings = get_settings()

RATE_LIMIT = 100          # requests
WINDOW_MS = 60_000        # 1 minute in milliseconds
WINDOW_S = 60

# Paths that bypass rate limiting entirely
_EXEMPT_PREFIXES = (
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# ── In-process fallback store (dev only) ─────────────────────────────────────
# {identifier: [timestamp_ms, ...]}
_local_store: dict[str, list[float]] = {}


def _local_check(identifier: str, now_ms: float) -> tuple[int, int]:
    """Returns (count_after_add, remaining). Mutates _local_store."""
    window_start = now_ms - WINDOW_MS
    timestamps = [t for t in _local_store.get(identifier, []) if t > window_start]
    timestamps.append(now_ms)
    _local_store[identifier] = timestamps
    count = len(timestamps)
    remaining = max(0, RATE_LIMIT - count)
    return count, remaining


async def _redis_check(redis, identifier: str, now_ms: float) -> tuple[int, int]:
    """
    Sliding-window check via Redis pipeline.
    Returns (count_after_add, remaining).
    """
    key = f"rl:{identifier}"
    window_start_ms = now_ms - WINDOW_MS

    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, window_start_ms)
    pipe.zadd(key, {str(now_ms): now_ms})
    pipe.zcard(key)
    pipe.expire(key, WINDOW_S + 1)
    results = await pipe.execute()

    count: int = results[2]
    remaining = max(0, RATE_LIMIT - count)
    return count, remaining


def _extract_identifier(request: Request) -> Optional[str]:
    """
    Derive a stable identifier for rate-limiting.
    Priority: API key prefix (from Authorization header) → user_id claim in JWT → IP.
    We don't decode the JWT here (too expensive in middleware); the API key prefix
    from the raw token value is enough to identify the caller.
    """
    auth: Optional[str] = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth[7:]
        # API keys look like  p1t_<48 hex>  or  p1l_<48 hex>
        # JWTs look like  eyJ...  (base64url)
        if token.startswith("p1"):
            # Use first 16 chars as a stable, non-secret identifier
            return f"apikey:{token[:16]}"
        # For JWTs we fall through to IP — user_id extraction is too expensive here
    # Fallback: client IP
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return f"ip:{forwarded_for.split(',')[0].strip()}"
    client = request.client
    if client:
        return f"ip:{client.host}"
    return "ip:unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Must be registered AFTER RequestIDMiddleware so X-Request-ID is set.
    Add to create_app() in main.py:
        app.add_middleware(RateLimitMiddleware)
    """

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self._redis = redis_client  # injected at startup; None → local fallback

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip exempt paths
        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        identifier = _extract_identifier(request)
        now_ms = time.time() * 1000

        try:
            if self._redis is not None:
                count, remaining = await _redis_check(self._redis, identifier, now_ms)
            else:
                count, remaining = _local_check(identifier, now_ms)
        except Exception as exc:
            # Redis error → fail open (don't block requests)
            log.warning("rate_limit.backend_error", exc_info=exc)
            return await call_next(request)

        reset_ts = int((now_ms + WINDOW_MS) / 1000)

        if count > RATE_LIMIT:
            log.warning("rate_limit.exceeded", identifier=identifier, count=count)
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error_code": "rate_limit_exceeded",
                    "message": "Too many requests. Limit is 100 requests per minute.",
                    "request_id": request.headers.get("X-Request-ID"),
                },
                headers={
                    "X-RateLimit-Limit": str(RATE_LIMIT),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_ts),
                    "Retry-After": str(WINDOW_S),
                },
            )

        response: Response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_ts)
        return response