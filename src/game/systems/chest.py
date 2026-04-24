"""Chest opening — rolls loot tables for treasure-chest items.

Decoupled from the economy/shop module: chests are inventory items the
player consumes, not a transaction. The shop happens to *sell* chests, but
opening one is a separate concern that belongs here.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from src.data.registry import registry
from src.game.engine.drop import roll_drops


# Chest item_key → loot-table key in the registry. Add new chest tiers here.
_CHEST_LOOT_TABLE: dict[str, str] = {
    "ChestHoang": "LootChestHoang",
    "ChestHuyen": "LootChestHuyen",
    "ChestDia":   "LootChestDia",
    "ChestThien": "LootChestThien",
    # Đan Lô chests — drop the unique furnace of matching grade, with a
    # secondary chance at the normal furnace and auxiliary herbs/pills.
    "ChestDanLoG1":  "LootChestDanLoG1",
    "ChestDanLoG2":  "LootChestDanLoG2",
    "ChestDanLoG3":  "LootChestDanLoG3",
    "ChestDanLoG4":  "LootChestDanLoG4",
    # Themed chests — mostly herb-heavy with a rare Đan Lô chance.
    "ChestDuocVien": "LootChestDuocVien",
    "ChestLuyenDan": "LootChestLuyenDan",
    # World-boss realm chests — drop forge_materials, pill recipes, and
    # uniques scoped to the matching cultivation realm (1-9).
    "ChestWorldBossR1": "LootChestWorldBossR1",
    "ChestWorldBossR2": "LootChestWorldBossR2",
    "ChestWorldBossR3": "LootChestWorldBossR3",
    "ChestWorldBossR4": "LootChestWorldBossR4",
    "ChestWorldBossR5": "LootChestWorldBossR5",
    "ChestWorldBossR6": "LootChestWorldBossR6",
    "ChestWorldBossR7": "LootChestWorldBossR7",
    "ChestWorldBossR8": "LootChestWorldBossR8",
    "ChestWorldBossR9": "LootChestWorldBossR9",
}


@dataclass
class ChestOpenResult:
    ok: bool
    message: str
    loot: list[dict]  # [{"item_key": str, "quantity": int}]


def open_chest(chest_key: str, rng: random.Random | None = None) -> ChestOpenResult:
    """Roll loot for a chest item.

    Args:
        chest_key:  Item key of the chest being opened (e.g. "ChestHoang").
        rng:        Optional seeded RNG for deterministic results (tests).

    Returns:
        ChestOpenResult with ok=True and a non-empty loot list on success,
        or ok=False with an error message if the chest key is unknown or its
        registered loot table is missing.
    """
    loot_table_key = _CHEST_LOOT_TABLE.get(chest_key)
    if not loot_table_key:
        return ChestOpenResult(
            ok=False,
            message=f"Không tìm thấy bảng loot cho {chest_key}.",
            loot=[],
        )

    drop_table = registry.get_loot_table(loot_table_key)
    if not drop_table:
        return ChestOpenResult(
            ok=False,
            message=f"Bảng loot {loot_table_key} trống.",
            loot=[],
        )

    rng = rng or random.Random()
    loot = roll_drops(drop_table, rng).merge()
    return ChestOpenResult(ok=True, message="", loot=loot)
