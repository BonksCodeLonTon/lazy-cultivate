"""Economy system — shop inventory, purchases, chest opening, and currency operations."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.data.registry import registry
from src.game.constants.currencies import CURRENCY_CAP, CELESTIAL_DAO_COST
from src.game.constants.grades import Grade
from src.game.engine.drop import roll_drops


# ── Fixed shop slots (Gian Cố Định — 13 items, never reset) ─────────────────
FIXED_SHOP_ITEMS: list[dict] = [
    {"item_key": "DanHoiHPSmall",  "grade": 1, "price": 300,   "currency": "merit", "stock": -1},
    {"item_key": "DanHoiMPSmall",  "grade": 1, "price": 300,   "currency": "merit", "stock": -1},
    {"item_key": "DanHoiHPMid",    "grade": 2, "price": 800,   "currency": "merit", "stock": -1},
    {"item_key": "DanHoiMPMid",    "grade": 2, "price": 800,   "currency": "merit", "stock": -1},
    {"item_key": "DanCurePoison",  "grade": 1, "price": 500,   "currency": "merit", "stock": -1},
    {"item_key": "DanCureCC",      "grade": 1, "price": 500,   "currency": "merit", "stock": -1},
    {"item_key": "DanCureBleeds",  "grade": 1, "price": 400,   "currency": "merit", "stock": -1},
    {"item_key": "ChestHoang",     "grade": 1, "price": 2000,  "currency": "merit", "stock": -1},
    {"item_key": "ChestHuyen",     "grade": 2, "price": 8000,  "currency": "merit", "stock": -1},
    {"item_key": "ScrollAtkHoang", "grade": 1, "price": 1000,  "currency": "merit", "stock": -1},
    {"item_key": "ScrollDefHoang", "grade": 1, "price": 1000,  "currency": "merit", "stock": -1},
    {"item_key": "ScrollSupHoang", "grade": 1, "price": 1000,  "currency": "merit", "stock": -1},
    {"item_key": "ItemTayNghiep",  "grade": 3, "price": 80000, "currency": "merit", "stock": -1},
]

# ── Dark market fixed slot ────────────────────────────────────────────────────
DARK_MARKET_FIXED = {
    "item_key": "BuffMeritX2_30days",
    "grade": 3,
    "price": CELESTIAL_DAO_COST,
    "currency": "karma_usable",
}

# ── Rotating pool for Gian Luân Chuyển (pick 6-8 random per reset) ──────────
ROTATING_POOL: list[dict] = [
    {"item_key": "DanHoiHPLarge",  "grade": 2, "price": 2000,  "currency": "merit"},
    {"item_key": "DanHoiMPLarge",  "grade": 2, "price": 2000,  "currency": "merit"},
    {"item_key": "DanBuffHP",      "grade": 2, "price": 2000,  "currency": "merit"},
    {"item_key": "DanBuffMP",      "grade": 2, "price": 2000,  "currency": "merit"},
    {"item_key": "DanBuffCrit",    "grade": 2, "price": 3000,  "currency": "merit"},
    {"item_key": "DanBuffSpeed",   "grade": 2, "price": 2500,  "currency": "merit"},
    {"item_key": "DanBuffRes",     "grade": 2, "price": 2500,  "currency": "merit"},
    {"item_key": "DanHoiHPMP",     "grade": 2, "price": 1500,  "currency": "merit"},
    {"item_key": "DanHoiFull",     "grade": 3, "price": 8000,  "currency": "merit"},
    {"item_key": "DanBuffFDmg",    "grade": 3, "price": 5000,  "currency": "merit"},
    {"item_key": "DanBuffShield",  "grade": 3, "price": 6000,  "currency": "merit"},
    {"item_key": "ChestDia",       "grade": 3, "price": 30000, "currency": "merit"},
    {"item_key": "ScrollAtkHuyen", "grade": 2, "price": 3000,  "currency": "merit"},
    {"item_key": "ScrollDefHuyen", "grade": 2, "price": 3000,  "currency": "merit"},
    {"item_key": "ScrollSupHuyen", "grade": 2, "price": 3000,  "currency": "merit"},
    {"item_key": "ItemPhaCanh",    "grade": 3, "price": 50000, "currency": "merit"},
]

# ── Dark market rotating pool (karma items) ──────────────────────────────────
DARK_POOL: list[dict] = [
    {"item_key": "DanDebuffFire",  "grade": 2, "price": 5000,  "currency": "karma_usable"},
    {"item_key": "DanDebuffIce",   "grade": 2, "price": 5000,  "currency": "karma_usable"},
    {"item_key": "DanDebuffMute",  "grade": 2, "price": 5000,  "currency": "karma_usable"},
    {"item_key": "DanKarmaDown",   "grade": 3, "price": 20000, "currency": "karma_usable"},
    {"item_key": "DanHoiFull",     "grade": 3, "price": 15000, "currency": "karma_usable"},
    {"item_key": "DanBuffFDmg",    "grade": 3, "price": 10000, "currency": "karma_usable"},
    {"item_key": "ChestHuyen",     "grade": 2, "price": 12000, "currency": "karma_usable"},
    {"item_key": "MatCanNguyen",   "grade": 2, "price": 8000,  "currency": "karma_usable"},
]


@dataclass
class ShopSlot:
    item_key: str
    grade: int
    price: int
    currency: str   # "merit" | "karma_usable" | "primordial_stones"
    stock: int = -1  # -1 = unlimited


def get_fixed_shop() -> list[ShopSlot]:
    return [ShopSlot(**s) for s in FIXED_SHOP_ITEMS]


def get_rotating_shop(seed: int | None = None) -> list[ShopSlot]:
    rng = random.Random(seed)
    count = rng.randint(6, 8)
    chosen = rng.sample(ROTATING_POOL, min(count, len(ROTATING_POOL)))
    return [ShopSlot(**s) for s in chosen]


def get_dark_market(seed: int | None = None) -> tuple[ShopSlot, list[ShopSlot]]:
    fixed = ShopSlot(**DARK_MARKET_FIXED)
    rng = random.Random(seed)
    count = rng.randint(5, min(8, len(DARK_POOL)))
    rotating = [ShopSlot(**s) for s in rng.sample(DARK_POOL, count)]
    return fixed, rotating


@dataclass
class PurchaseResult:
    ok: bool
    message: str
    item_key: str = ""
    quantity: int = 0


def purchase(
    player,  # ORM Player
    slot: ShopSlot,
    quantity: int = 1,
) -> PurchaseResult:
    """Validate and deduct currency for a shop purchase. Does NOT add to inventory — caller does that."""
    total = slot.price * quantity
    currency_val = getattr(player, slot.currency, 0)

    if currency_val < total:
        currency_name = {
            "merit": "Công Đức",
            "karma_usable": "Nghiệp Lực Khả Dụng",
            "primordial_stones": "Hỗn Nguyên Thạch",
        }.get(slot.currency, slot.currency)
        return PurchaseResult(
            ok=False,
            message=f"Không đủ {currency_name}. Cần {total:,}, hiện có {currency_val:,}.",
        )

    setattr(player, slot.currency, currency_val - total)
    return PurchaseResult(ok=True, message="", item_key=slot.item_key, quantity=quantity)


# ── Chest loot table mapping (item_key → loot table key) ─────────────────────
_CHEST_LOOT_TABLE: dict[str, str] = {
    "ChestHoang": "LootChestHoang",
    "ChestHuyen": "LootChestHuyen",
    "ChestDia":   "LootChestDia",
    "ChestThien": "LootChestThien",
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
        or ok=False with an error message if the chest key is unknown.
    """
    loot_table_key = _CHEST_LOOT_TABLE.get(chest_key)
    if not loot_table_key:
        return ChestOpenResult(ok=False, message=f"Không tìm thấy bảng loot cho {chest_key}.", loot=[])

    drop_table = registry.get_loot_table(loot_table_key)
    if not drop_table:
        return ChestOpenResult(ok=False, message=f"Bảng loot {loot_table_key} trống.", loot=[])

    rng = rng or random.Random()
    loot = roll_drops(drop_table, rng).merge()
    return ChestOpenResult(ok=True, message="", loot=loot)
