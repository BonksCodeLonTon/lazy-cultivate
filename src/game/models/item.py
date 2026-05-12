"""Item model."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

from src.game.constants.grades import Grade


class ItemType(StrEnum):
    GEM = "gem"                # Formation gem (Ngọc Khảm)
    SCROLL = "scroll"          # Skill scroll (Ngọc Giản)
    CHEST = "chest"            # Loot chest (Rương)
    ARTIFACT = "artifact"      # Pháp Bảo
    SPECIAL = "special"        # Special items (Phá Cảnh Đan, Hỗn Nguyên Thạch...)
    HERB = "herb"              # Luyện đan ingredient (Thảo Dược + tinh hoa yêu thú)
    PILL = "pill"              # Đan Dược — covers crafted cultivation pills + healing pills (category="heal")
    FURNACE = "furnace"        # Đan Lô — alchemy tool (enables/boosts pill crafting)


@dataclass(frozen=True)
class ItemTemplate:
    key: str
    vi: str
    en: str
    item_type: ItemType
    grade: Grade
    shop_price_merit: int = 0         # Reference price in Công Đức (for fee calc)
    shop_price_stones: int = 0        # Reference price in Hỗn Nguyên Thạch
    stack_max: int = 9999
    description_vi: str = ""


@dataclass
class InventoryEntry:
    item_key: str
    quantity: int
    grade: Grade
