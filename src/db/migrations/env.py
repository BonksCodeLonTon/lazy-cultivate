"""Alembic environment — async SQLAlchemy with PostgreSQL."""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import quote_plus

# asyncpg on Windows requires SelectorEventLoop (ProactorEventLoop breaks getaddrinfo)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from alembic import context
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models.base import Base

# Import all models so metadata is populated
import src.db.models.player  # noqa: F401
import src.db.models.turn_tracker  # noqa: F401
import src.db.models.inventory  # noqa: F401
import src.db.models.skill  # noqa: F401
import src.db.models.artifact  # noqa: F401
import src.db.models.formation  # noqa: F401
import src.db.models.market  # noqa: F401

# Load .env from project root (two levels up from this file)
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _build_database_url() -> str:
    host = os.environ["DB_HOST"]
    port = os.environ.get("DB_PORT", "5432")
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    name = os.environ["DB_NAME"]
    # asyncpg on Windows can fail to resolve 'localhost' in async context; use IP directly
    if host == "localhost":
        host = "127.0.0.1"
    # URL-encode user/password — special chars like @ or # in passwords break URL parsing
    return f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{name}"


DATABASE_URL = os.environ.get("DATABASE_URL") or _build_database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DATABASE_URL, connect_args={"ssl": False})
    async with engine.connect() as conn:
        await conn.run_sync(_run_sync_migrations)
    await engine.dispose()


def _run_sync_migrations(sync_conn) -> None:
    context.configure(connection=sync_conn, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
