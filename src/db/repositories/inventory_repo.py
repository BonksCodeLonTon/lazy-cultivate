"""Inventory repository."""
from __future__ import annotations

from sqlalchemy import delete, select, update
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

    async def try_remove_item(
        self, player_id: int, item_key: str, grade: Grade, quantity: int,
    ) -> bool:
        """Atomically decrement inventory iff stock ``>= quantity``.

        Single conditional UPDATE — Postgres takes a row lock for the
        write so concurrent ``try_remove_item`` calls serialize on it.
        Two simultaneous market-listing submissions for the same stack
        can no longer both pass: one decrements (rowcount=1, returns True),
        the other matches no rows (rowcount=0, returns False). Replaces
        the legacy ``has_item`` + ``remove_item`` two-step which had a
        race window between the SELECT and the UPDATE.
        """
        if quantity <= 0:
            return False

        # Conditional decrement — only matches if stock covers the request.
        update_stmt = (
            update(InventoryItem)
            .where(
                InventoryItem.player_id == player_id,
                InventoryItem.item_key == item_key,
                InventoryItem.grade == grade.value,
                InventoryItem.quantity >= quantity,
            )
            .values(quantity=InventoryItem.quantity - quantity)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(update_stmt)
        if result.rowcount == 0:
            return False

        # Sweep the row if it now reads zero. Rare edge case — most
        # inventory items decrement from many to fewer; catches the
        # exact-stock case where stock was equal to quantity.
        await self._session.execute(
            delete(InventoryItem)
            .where(
                InventoryItem.player_id == player_id,
                InventoryItem.item_key == item_key,
                InventoryItem.grade == grade.value,
                InventoryItem.quantity == 0,
            )
            .execution_options(synchronize_session=False)
        )
        return True
