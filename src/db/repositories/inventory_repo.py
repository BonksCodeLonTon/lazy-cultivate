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

    async def remove_any_grade(
        self, player_id: int, item_key: str, quantity: int = 1,
    ) -> bool:
        """Remove ``quantity`` of ``item_key`` across **any** grade row.

        Used for stackable ingredient items (herbs / yêu thú) where the
        intrinsic grade lives on the item template, not on each inventory
        row — historical drops stored these at their template grade (1-6)
        while alchemy validates with a single-bucket assumption. Iterating
        rows ascending-by-grade lets us decrement greedily and still
        succeed when stock is split across grades. Returns False only if
        the total summed quantity is insufficient.
        """
        rows = (
            await self._session.execute(
                select(InventoryItem)
                .where(
                    InventoryItem.player_id == player_id,
                    InventoryItem.item_key == item_key,
                )
                .order_by(InventoryItem.grade.asc())
            )
        ).scalars().all()
        total = sum(r.quantity for r in rows)
        if total < quantity:
            return False

        remaining = quantity
        for row in rows:
            if remaining <= 0:
                break
            take = min(row.quantity, remaining)
            row.quantity -= take
            remaining -= take
            if row.quantity == 0:
                await self._session.delete(row)
        return True

    async def has_item(self, player_id: int, item_key: str, grade: Grade, quantity: int = 1) -> bool:
        item = await self.get_item(player_id, item_key, grade)
        return item is not None and item.quantity >= quantity
