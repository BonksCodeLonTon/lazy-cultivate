"""Inventory repository."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.inventory import InventoryItem
from src.game.constants.grades import Grade


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self, player_id: int) -> list[InventoryItem]:
        result = await self._session.execute(
            select(InventoryItem).where(InventoryItem.player_id == player_id)
        )
        return list(result.scalars().all())

    async def get_item(self, player_id: int, item_key: str, grade: Grade) -> InventoryItem | None:
        result = await self._session.execute(
            select(InventoryItem).where(
                InventoryItem.player_id == player_id,
                InventoryItem.item_key == item_key,
                InventoryItem.grade == grade.value,
            )
        )
        return result.scalar_one_or_none()

    async def add_item(self, player_id: int, item_key: str, grade: Grade, quantity: int = 1) -> InventoryItem:
        existing = await self.get_item(player_id, item_key, grade)
        if existing:
            existing.quantity += quantity
            return existing

        item = InventoryItem(
            player_id=player_id,
            item_key=item_key,
            grade=grade.value,
            quantity=quantity,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def remove_item(self, player_id: int, item_key: str, grade: Grade, quantity: int = 1) -> bool:
        """Remove quantity from inventory. Returns False if insufficient."""
        existing = await self.get_item(player_id, item_key, grade)
        if not existing or existing.quantity < quantity:
            return False

        existing.quantity -= quantity
        if existing.quantity == 0:
            await self._session.delete(existing)

        return True

    async def has_item(self, player_id: int, item_key: str, grade: Grade, quantity: int = 1) -> bool:
        item = await self.get_item(player_id, item_key, grade)
        return item is not None and item.quantity >= quantity
