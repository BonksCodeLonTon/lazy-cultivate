"""Database connection setup (SQLAlchemy async + PostgreSQL)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.db.models.base import Base
from src.utils.config import settings

# Import all models so Base.metadata knows about every table
import src.db.models.player  # noqa: F401
import src.db.models.turn_tracker  # noqa: F401
import src.db.models.inventory  # noqa: F401
import src.db.models.skill  # noqa: F401
import src.db.models.artifact  # noqa: F401
import src.db.models.formation  # noqa: F401
import src.db.models.market  # noqa: F401

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
    """Create all tables (dev/test only — use Alembic migrations in production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
