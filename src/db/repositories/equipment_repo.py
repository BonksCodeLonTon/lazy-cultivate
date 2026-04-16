"""Equipment repository — manage ItemInstance records (equip / unequip / bag)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.item_instance import ItemInstance


class EquipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_instance(self, instance_id: int, player_id: int) -> ItemInstance | None:
        result = await self._session.execute(
            select(ItemInstance).where(
                ItemInstance.id == instance_id,
                ItemInstance.player_id == player_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_bag(self, player_id: int) -> list[ItemInstance]:
        result = await self._session.execute(
            select(ItemInstance).where(
                ItemInstance.player_id == player_id,
                ItemInstance.location == "bag",
            )
        )
        return list(result.scalars().all())

    async def get_equipped(self, player_id: int) -> list[ItemInstance]:
        result = await self._session.execute(
            select(ItemInstance).where(
                ItemInstance.player_id == player_id,
                ItemInstance.location == "equipped",
            )
        )
        return list(result.scalars().all())

    async def get_slot(self, player_id: int, slot: str) -> ItemInstance | None:
        """Item currently equipped in a specific slot, or None."""
        result = await self._session.execute(
            select(ItemInstance).where(
                ItemInstance.player_id == player_id,
                ItemInstance.location == "equipped",
                ItemInstance.slot == slot,
            )
        )
        return result.scalar_one_or_none()

    # ── Write ─────────────────────────────────────────────────────────────────

    async def add_to_bag(self, player_id: int, item_data: dict) -> ItemInstance:
        """Create a new ItemInstance in the player's bag from a generator dict.

        `item_data` must include 'slot' (the item's equipment slot type).
        """
        inst = ItemInstance(
            player_id=player_id,
            location="bag",
            slot=item_data["slot"],           # always stored; never None
            base_key=item_data.get("base_key"),
            unique_key=item_data.get("unique_key"),
            affixes=item_data.get("affixes", []),
            computed_stats=item_data.get("computed_stats", {}),
            grade=item_data.get("grade", 1),
            display_name=item_data.get("display_name", ""),
        )
        self._session.add(inst)
        await self._session.flush()
        return inst

    async def equip(self, player_id: int, instance_id: int) -> ItemInstance | None:
        """Equip a bag item into its slot.

        Returns the displaced ItemInstance (now in bag) if a swap occurred, else None.
        Raises ValueError if instance not found or not in bag.
        """
        inst = await self.get_instance(instance_id, player_id)
        if inst is None or inst.location != "bag":
            raise ValueError(
                f"Vật phẩm ID {instance_id} không tìm thấy trong túi đồ."
            )

        target_slot = inst.slot  # always set on the instance

        # Displace old occupant if any
        old = await self.get_slot(player_id, target_slot)
        displaced: ItemInstance | None = None
        if old:
            old.location = "bag"
            displaced = old
            await self._session.flush()

        inst.location = "equipped"
        await self._session.flush()
        return displaced

    async def unequip(self, player_id: int, slot: str) -> ItemInstance | None:
        """Move the item in `slot` back to bag.  Returns the item or None."""
        inst = await self.get_slot(player_id, slot)
        if not inst:
            return None
        inst.location = "bag"
        await self._session.flush()
        return inst

    async def discard(self, player_id: int, instance_id: int) -> bool:
        """Delete a bag item permanently.  Returns True if deleted."""
        inst = await self.get_instance(instance_id, player_id)
        if inst is None or inst.location != "bag":
            return False
        await self._session.delete(inst)
        await self._session.flush()
        return True
