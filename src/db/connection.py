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
import src.db.models.skill_mastery  # noqa: F401
import src.db.models.artifact  # noqa: F401
import src.db.models.formation  # noqa: F401
import src.db.models.market  # noqa: F401
import src.db.models.item_instance  # noqa: F401
import src.db.models.world_boss  # noqa: F401
import src.db.models.reroll_tracker  # noqa: F401

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    # Pool tuning. Defaults (size=5, overflow=10) are too small for the
    # interaction-burst pattern of a Discord bot — a single ``/status``
    # callback can hold a connection for a few hundred ms while loading
    # the player's six-relationship chain. We trade a tiny bit of idle
    # memory for never blocking on pool acquisition during raids/events.
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    # Recycle connections every 30 min so PG / proxy idle-timeouts don't
    # surface as the next caller's ``OperationalError``. ``pool_pre_ping``
    # already guards stale conns, but recycling proactively avoids the
    # extra round-trip a ping costs on a cold checkout.
    pool_recycle=1800,
    # LIFO checkout keeps a smaller "warm" subset of connections in
    # rotation — better libpq plan-cache and TLS-session reuse than the
    # default FIFO under bursty load.
    pool_use_lifo=True,
    # SQLAlchemy compiles each query into a textual SQL string + bind
    # plan. The default cache holds 500 entries; this codebase has
    # enough variants (filtered selectinload, paginated market lists,
    # repository helpers, etc.) to benefit from a roomier cache.
    query_cache_size=1200,
    # asyncpg keeps a per-connection prepared-statement cache. Bumping
    # the limit lets the driver keep more frequently-used statements
    # warm, which is a win for high-fan-out endpoints (status,
    # inventory) that fire many distinct queries per request.
    connect_args={"statement_cache_size": 1000},
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

        # ── 0015: item_instances.quality column ────────────────────────────
        # Equipment views render the rarity icon from this. Auto-patch for
        # environments that haven't run Alembic — column ships with
        # ``DEFAULT 'hoan'`` so existing rows automatically inherit Hoàng Phẩm.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'item_instances'
                      AND column_name = 'quality'
                ) THEN
                    ALTER TABLE item_instances
                        ADD COLUMN quality VARCHAR(8) NOT NULL DEFAULT 'hoan';
                    RAISE NOTICE 'auto-patched item_instances.quality column';
                END IF;
            END$$;
        """))

        # ── 0016: players.constitution_tracker column ─────────────────────
        # Records every Thể Chất ever activated so players can swap freely
        # without re-paying activation cost. Pre-migration rows backfill from
        # ``constitution_type`` since anything currently equipped was
        # already unlocked by activation.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'players'
                      AND column_name = 'constitution_tracker'
                ) THEN
                    ALTER TABLE players
                        ADD COLUMN constitution_tracker VARCHAR(8192)
                        NOT NULL DEFAULT 'ConstitutionPhamThe';
                    UPDATE players
                       SET constitution_tracker = COALESCE(constitution_type, 'ConstitutionPhamThe');
                    RAISE NOTICE 'auto-patched players.constitution_tracker column';
                END IF;
            END$$;
        """))

        # ── 0017: players.shield_current column ────────────────────────────
        # Persistent Energy Shield carry-over between battles. New rows
        # default to 0 — the post-battle heal hooks set it to shield_max.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'players'
                      AND column_name = 'shield_current'
                ) THEN
                    ALTER TABLE players
                        ADD COLUMN shield_current INTEGER NOT NULL DEFAULT 0;
                    RAISE NOTICE 'auto-patched players.shield_current column';
                END IF;
            END$$;
        """))
