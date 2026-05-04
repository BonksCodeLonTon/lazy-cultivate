"""Economy system — shop inventory, purchases, and currency operations.

Chest-opening lives in ``chest.py`` — chests are sold here but consumed there.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from src.game.constants.currencies import CELESTIAL_DAO_COST


# ── Fixed shop slots (Gian Cố Định — never reset) ───────────────────────────
FIXED_SHOP_ITEMS: list[dict] = [
    {"item_key": "DanHoiHPSmall",   "grade": 1, "price": 300,    "currency": "merit", "stock": -1},
    {"item_key": "DanHoiMPSmall",   "grade": 1, "price": 300,    "currency": "merit", "stock": -1},
    {"item_key": "DanHoiHPMid",     "grade": 2, "price": 800,    "currency": "merit", "stock": -1},
    {"item_key": "DanHoiMPMid",     "grade": 2, "price": 800,    "currency": "merit", "stock": -1},
    {"item_key": "DanCurePoison",   "grade": 1, "price": 500,    "currency": "merit", "stock": -1},
    {"item_key": "DanCureCC",       "grade": 1, "price": 500,    "currency": "merit", "stock": -1},
    {"item_key": "DanCureBleeds",   "grade": 1, "price": 400,    "currency": "merit", "stock": -1},
    {"item_key": "ChestHoang",      "grade": 1, "price": 2000,   "currency": "merit", "stock": -1},
    {"item_key": "ChestHuyen",      "grade": 2, "price": 8000,   "currency": "merit", "stock": -1},
    {"item_key": "ItemTayNghiep",   "grade": 3, "price": 80000,  "currency": "merit", "stock": -1},
    # Normal Đan Lô — gateway tool for Luyện Đan. Bought once per grade.
    {"item_key": "DanLoThuong_G1",  "grade": 1, "price": 3000,   "currency": "merit", "stock": -1},
    {"item_key": "DanLoThuong_G2",  "grade": 2, "price": 18000,  "currency": "merit", "stock": -1},
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
    {"item_key": "ItemPhaCanh",    "grade": 3, "price": 50000, "currency": "merit"},
    # Higher-tier Đan Lô — rotate in the shop; rarer sightings.
    {"item_key": "DanLoThuong_G3", "grade": 3, "price": 120000, "currency": "merit"},
    {"item_key": "DanLoThuong_G4", "grade": 4, "price": 800000, "currency": "merit"},
    # Đan Lô chests — low-rotation so the unique furnaces remain a find.
    {"item_key": "ChestDanLoG1",   "grade": 1, "price": 25000,  "currency": "merit"},
    {"item_key": "ChestDanLoG2",   "grade": 2, "price": 120000, "currency": "merit"},
    {"item_key": "ChestDuocVien",  "grade": 2, "price": 40000,  "currency": "merit"},
    {"item_key": "ChestLuyenDan",  "grade": 3, "price": 180000, "currency": "merit"},
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


# ── Tàng Kinh Các — per-skill scroll shop (grade 1-2 only) ─────────────────
# Built dynamically from synthesized scrolls in registry.items so the catalog
# stays in lock-step with the player skill JSONs. Grade 3-4 scrolls never
# appear here — those drop only from Bí Cảnh loot.

def get_skill_scroll_shop() -> list[ShopSlot]:
    from src.data.registry import registry
    slots: list[ShopSlot] = []
    for item in registry.items.values():
        if item.get("type") != "scroll":
            continue
        if not item.get("taught_skill"):
            continue
        grade = int(item.get("grade", 0))
        if grade not in (1, 2):
            continue
        slots.append(ShopSlot(
            item_key=item["key"],
            grade=grade,
            price=int(item.get("shop_price_merit", 0)),
            currency="merit",
            stock=-1,
        ))
    # Stable sort: grade asc, then by skill realm asc, then key alphabetical.
    def sort_key(s: ShopSlot) -> tuple:
        skill_key = s.item_key.removeprefix("Scroll_")
        skill = registry.get_skill(skill_key)
        realm = skill.get("realm", 0) if skill else 0
        return (s.grade, realm, s.item_key)
    slots.sort(key=sort_key)
    return slots


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
