"""
app/db/session.py
Async SQLAlchemy engine + session factory.
SQLite for dev, PostgreSQL for staging/prod.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool

from app.core.config import get_settings

settings = get_settings()

# Use NullPool for SQLite (no connection pooling needed / not thread-safe across asyncio tasks)
_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
_is_postgres = settings.DATABASE_URL.startswith("postgresql")

# Supabase's connection pooler (Supavisor/PgBouncer) runs in transaction-pooling
# mode, which doesn't support asyncpg's prepared statements. Disabling the
# statement cache avoids DuplicatePreparedStatementError under the pooler.
# Must be passed via connect_args as a real int — passing it as a
# "?statement_cache_size=0" query string param breaks asyncpg's internal
# validation, which compares it against 0 assuming an int, not a str.
_connect_args = {"statement_cache_size": 0} if _is_postgres else {}

engine = create_async_engine(
    settings.DATABASE_URL.split("?")[0],
    echo=settings.DEBUG,
    future=True,
    poolclass=NullPool if _is_sqlite else AsyncAdaptedQueuePool,
    connect_args=_connect_args,
    **({} if _is_sqlite else {
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_timeout": settings.DATABASE_POOL_TIMEOUT,
        "pool_pre_ping": True,   # verify connections before use
        "pool_recycle": 1800,    # recycle stale connections every 30 min
    }),
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session and closes it after use."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
