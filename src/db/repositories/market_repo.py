"""Market listing repository."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.market import MarketListing
from src.game.constants.currencies import MARKET_MAX_LISTINGS
from src.game.constants.grades import Grade


class MarketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, listing_id: int) -> MarketListing | None:
        result = await self._session.execute(
            select(MarketListing).where(MarketListing.id == listing_id)
        )
        return result.scalar_one_or_none()

    async def get_active_by_seller(self, seller_id: int) -> list[MarketListing]:
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(MarketListing).where(
                MarketListing.seller_id == seller_id,
                MarketListing.expires_at > now,
            )
        )
        return list(result.scalars().all())

    async def count_active_by_seller(self, seller_id: int) -> int:
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(func.count(MarketListing.id)).where(
                MarketListing.seller_id == seller_id,
                MarketListing.expires_at > now,
            )
        )
        return result.scalar_one()

    async def browse(
        self,
        grade: Grade | None = None,
        item_key: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[MarketListing]:
        now = datetime.now(timezone.utc)
        stmt = select(MarketListing).where(MarketListing.expires_at > now)
        if grade is not None:
            stmt = stmt.where(MarketListing.grade == grade.value)
        if item_key is not None:
            stmt = stmt.where(MarketListing.item_key == item_key)
        stmt = stmt.order_by(MarketListing.price.asc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, listing: MarketListing) -> MarketListing:
        self._session.add(listing)
        await self._session.flush()
        return listing

    async def delete(self, listing: MarketListing) -> None:
        await self._session.delete(listing)

    async def can_create_listing(self, seller_id: int) -> tuple[bool, str]:
        count = await self.count_active_by_seller(seller_id)
        if count >= MARKET_MAX_LISTINGS:
            return False, f"Đã đạt giới hạn {MARKET_MAX_LISTINGS} đơn hàng đang niêm yết."
        return True, ""

    async def purge_expired(self) -> int:
        """Delete expired listings. Returns count removed."""
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(MarketListing).where(MarketListing.expires_at <= now)
        )
        expired = result.scalars().all()
        for listing in expired:
            await self._session.delete(listing)
        return len(expired)
