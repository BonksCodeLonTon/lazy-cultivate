"""Database connection setup (SQLAlchemy async + PostgreSQL)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.db.models.base import Base
from src.utils.config import settings

log = logging.getLogger(__name__)

# Import all models so Base.metadata knows about every table
import src.db.models.player  # noqa: F401
import src.db.models.turn_tracker  # noqa: F401
import src.db.models.inventory  # noqa: F401
import src.db.models.skill  # noqa: F401
import src.db.models.artifact  # noqa: F401
import src.db.models.formation  # noqa: F401
import src.db.models.market  # noqa: F401
import src.db.models.item_instance  # noqa: F401
import src.db.models.world_boss  # noqa: F401

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables and auto-patch known ORM/schema drift.

    Dev/test convenience only — production must run ``alembic upgrade head``.
    ``Base.metadata.create_all`` doesn't ALTER existing columns, so any time
    a column's ``String(N)`` changes in the ORM we either need an Alembic
    migration to run or this block to self-heal. Each patch is idempotent
    (gated by an ``information_schema`` check) so it's safe on every start.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # ── 0009: players.constitution_type VARCHAR(64) → VARCHAR(512) ────
        # Thể Tu can now hold up to 8 comma-separated legendary keys
        # (~209 chars). Older dev DBs from before this change still have 64.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'players'
                      AND column_name = 'constitution_type'
                      AND character_maximum_length < 512
                ) THEN
                    ALTER TABLE players
                        ALTER COLUMN constitution_type TYPE VARCHAR(512);
                    RAISE NOTICE 'auto-patched players.constitution_type to VARCHAR(512)';
                END IF;
            END$$;
        """))
