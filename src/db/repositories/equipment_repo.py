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

    async def get_by_id(self, instance_id: int) -> ItemInstance | None:
        """Fetch any ItemInstance by ID regardless of owner (for market operations)."""
        result = await self._session.execute(
            select(ItemInstance).where(ItemInstance.id == instance_id)
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

    async def equip(self, player_id: int, instance_id: int) -> list["ItemInstance"]:
        """Equip a bag item into its slot.

        Returns the list of ItemInstances that were displaced back to the bag
        as a side-effect. Typically zero or one entry; for 2H weapons both a
        previous weapon AND a previous off-hand can be displaced in one call,
        and for an off-hand that replaces a 2H weapon both slots are freed too.

        Raises ValueError if instance not found or not in bag.
        """
        from src.data.registry import registry

        inst = await self.get_instance(instance_id, player_id)
        if inst is None or inst.location != "bag":
            raise ValueError(
                f"Vật phẩm ID {instance_id} không tìm thấy trong túi đồ."
            )

        target_slot = inst.slot  # always set on the instance
        base_data = registry.get_base(inst.base_key) if inst.base_key else None
        is_two_handed = bool(base_data and base_data.get("two_handed", False))

        displaced: list[ItemInstance] = []

        # Displace current occupant of the target slot
        old = await self.get_slot(player_id, target_slot)
        if old:
            old.location = "bag"
            displaced.append(old)

        # Mutual exclusion: 2H weapon + off-hand cannot coexist
        if target_slot == "weapon" and is_two_handed:
            # Also free the off-hand slot
            old_off = await self.get_slot(player_id, "off_hand")
            if old_off:
                old_off.location = "bag"
                displaced.append(old_off)
        elif target_slot == "off_hand":
            # If a 2H weapon is equipped, must free it first
            current_weapon = await self.get_slot(player_id, "weapon")
            if current_weapon and current_weapon.base_key:
                wbase = registry.get_base(current_weapon.base_key)
                if wbase and wbase.get("two_handed", False):
                    current_weapon.location = "bag"
                    displaced.append(current_weapon)

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
