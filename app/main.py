"""
app/main.py
Application factory — the single entry point for the FastAPI app.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import PlayerOnePayError
from app.core.logging import configure_logging, get_logger
from app.middleware.request_id import RequestIDMiddleware

settings = get_settings()
log = get_logger(__name__)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    try:
        JSONResponse(content=value)
        return value
    except TypeError:
        return str(value)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Runs at startup and shutdown."""
    configure_logging()
    log.info("app.starting", version=settings.APP_VERSION, env=settings.APP_ENV)


    # Sentry (production error tracking)
    if settings.SENTRY_DSN and settings.is_production:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.2)
        log.info("sentry.initialised")

    # Mock bank webhook outbox dispatcher (dev/test only)
    dispatcher_task = None
    if not settings.is_production and settings.PAYMENT_PROVIDER == "mock":
        async def _run_dispatcher() -> None:
            from app.db.session import AsyncSessionLocal
            from app.providers.mock_bank.dispatcher import dispatch_pending
            interval = settings.MOCK_BANK_DISPATCHER_INTERVAL_SECONDS
            while True:
                try:
                    async with AsyncSessionLocal() as session:
                        delivered = await dispatch_pending(session)
                        if delivered:
                            log.debug("mock_bank.outbox.dispatched", count=delivered)
                except Exception as exc:
                    log.warning("mock_bank.outbox.error", exc_info=exc)
                await asyncio.sleep(interval)

        dispatcher_task = asyncio.create_task(_run_dispatcher())
        log.info("mock_bank.dispatcher.started", interval=settings.MOCK_BANK_DISPATCHER_INTERVAL_SECONDS)

    yield

    log.info("app.shutdown")
    if dispatcher_task:
        dispatcher_task.cancel()
        try:
            await dispatcher_task
        except asyncio.CancelledError:
            pass
    from app.db.session import engine
    await engine.dispose()


# ── Factory ───────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "PlayerOnePay v2 — production-grade payment API. "
            "All endpoints are versioned under `/api/v1`."
        ),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware (order matters — outermost registered = outermost executed) ─
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time-ms"],
    )
    app.add_middleware(RequestIDMiddleware)

    # ── Exception Handlers ────────────────────────────────────────────────────

    @app.exception_handler(PlayerOnePayError)
    async def domain_error_handler(request: Request, exc: PlayerOnePayError) -> JSONResponse:
        log.warning(
            "domain_error",
            error_code=exc.error_code,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error_code": exc.error_code,
                "message": exc.message,
                "detail": exc.detail,
                "request_id": request.headers.get("X-Request-ID"),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error_code": "validation_error",
                "message": "Request validation failed.",
                "detail": _json_safe(exc.errors()),
                "request_id": request.headers.get("X-Request-ID"),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", exc_info=exc, path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error_code": "internal_error",
                "message": "An unexpected error occurred. Please try again.",
                "request_id": request.headers.get("X-Request-ID"),
            },
        )

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # Mock bank sandbox router (dev/test only)
    if not settings.is_production:
        from app.providers.mock_bank.router import router as mock_bank_router
        app.include_router(mock_bank_router, prefix=settings.API_V1_PREFIX)

    # ── Health / Readiness probes ─────────────────────────────────────────────
    @app.api_route("/health", methods=["GET", "HEAD"], tags=["Observability"], include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "version": settings.APP_VERSION}

    @app.get("/ready", tags=["Observability"], include_in_schema=False)
    async def ready(request: Request) -> dict:
        from app.db.session import engine
        try:
            async with engine.connect():
                pass
            db_ok = True
        except Exception:
            db_ok = False
        ready = db_ok
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "db": db_ok},
        )

    @app.get("/", tags=["Root"], include_in_schema=False)
    async def root() -> dict:
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()