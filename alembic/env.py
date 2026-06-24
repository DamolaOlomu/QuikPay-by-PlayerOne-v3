"""
alembic/env.py
Async-aware Alembic environment for SQLAlchemy 2.x.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import the shared Base, then explicitly register every model on its
# metadata so Alembic autogenerate can see them. base_models is only ever
# imported here — never from app.db.base itself or app runtime code — to
# avoid a circular import (see app/db/base_models.py docstring).
from app.db.base import Base  # noqa: F401
import app.db.base_models  # noqa: F401
from app.core.config import get_settings

settings = get_settings()
config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.split("?")[0])

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # statement_cache_size=0 — Supabase's pooler (Supavisor/PgBouncer) runs in
    # transaction-pooling mode, which doesn't support asyncpg's prepared
    # statements. Must be passed as a real int via connect_args, not as a
    # "?statement_cache_size=0" query param on the URL (asyncpg's internal
    # validation compares it against 0 assuming an int, and breaks on a str).
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
