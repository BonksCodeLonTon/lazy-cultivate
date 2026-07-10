"""Tông Môn shop — Tàng Bảo Các catalog composition (pure logic, no DB).

Three sections, all priced in Cống Hiến:

* **fixed**    — always available (``sect_shop.json["fixed"]``).
* **rotating** — a deterministic weekly sample from ``["rotating"]``; slot
  count comes from the Tụ Bảo Các facility (+1 per 2 levels), premium pool
  entries additionally gate on ``min_tu_bao_level``. The sample is seeded by
  ``(sect_id, week_key)`` so every member sees the same stock all week and a
  bot restart cannot reroll it.
* **scroll**   — the grade-1/2 skill-scroll catalog re-priced from merit to
  Cống Hiến, gated and discounted by the Tàng Kinh Các facility level.

The atomic purchase (CH deduction + weekly-limit ledger) lives in
``SectRepository.purchase_shop_item_atomic``; this module only decides *what
is on the shelves and at what price*.
"""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass
from math import ceil

# Tụ Bảo Các: +1 rotating slot per 2 facility levels (L10 → 5 slots).
ROTATING_SLOTS_PER_LEVELS = 2

# Tàng Kinh Các gates: L1 opens grade-1 scrolls, L3 opens grade-2;
# L6+ discounts scroll CH prices 10%, L10 20%.
SCROLL_GRADE1_MIN_LEVEL = 1
SCROLL_GRADE2_MIN_LEVEL = 3
SCROLL_DISCOUNT_L6 = 0.10
SCROLL_DISCOUNT_L10 = 0.20

# Merit → Cống Hiến conversion for scroll rows (docs §3.7: price_merit / 10).
SCROLL_MERIT_PER_CH = 10


@dataclass(frozen=True)
class SectShopSlot:
    item_key: str
    grade: int
    price_ch: int
    weekly_limit: int = 0        # 0 = unlimited
    section: str = "fixed"       # fixed | rotating | scroll


def _catalog() -> dict[str, list[dict]]:
    from src.data.registry import registry
    return registry.sect_shop


def fixed_slots() -> list[SectShopSlot]:
    return [
        SectShopSlot(
            item_key=e["item_key"],
            grade=int(e.get("grade", 1)),
            price_ch=int(e["price_ch"]),
            weekly_limit=int(e.get("weekly_limit", 0)),
            section="fixed",
        )
        for e in _catalog().get("fixed", [])
    ]


def rotating_slot_count(tu_bao_level: int) -> int:
    return max(0, int(tu_bao_level)) // ROTATING_SLOTS_PER_LEVELS


def rotation_seed(sect_id: int, week_key: str) -> int:
    """Deterministic across restarts — NEVER use ``hash()`` here (randomized
    per process), crc32 is stable."""
    return zlib.crc32(f"{sect_id}:{week_key}".encode("utf-8"))


def rotating_slots(sect_id: int, week_key: str, tu_bao_level: int) -> list[SectShopSlot]:
    count = rotating_slot_count(tu_bao_level)
    if count <= 0:
        return []
    pool = [
        e for e in _catalog().get("rotating", [])
        if int(e.get("min_tu_bao_level", 0)) <= int(tu_bao_level)
    ]
    if not pool:
        return []
    rng = random.Random(rotation_seed(sect_id, week_key))
    chosen = rng.sample(pool, min(count, len(pool)))
    return [
        SectShopSlot(
            item_key=e["item_key"],
            grade=int(e.get("grade", 1)),
            price_ch=int(e["price_ch"]),
            weekly_limit=int(e.get("weekly_limit", 0)),
            section="rotating",
        )
        for e in chosen
    ]


def scroll_grade_cap(tang_kinh_level: int) -> int:
    """Highest scroll grade purchasable with CH at this Tàng Kinh Các level."""
    if tang_kinh_level >= SCROLL_GRADE2_MIN_LEVEL:
        return 2
    if tang_kinh_level >= SCROLL_GRADE1_MIN_LEVEL:
        return 1
    return 0


def scroll_discount(tang_kinh_level: int) -> float:
    if tang_kinh_level >= 10:
        return SCROLL_DISCOUNT_L10
    if tang_kinh_level >= 6:
        return SCROLL_DISCOUNT_L6
    return 0.0


def scroll_price_ch(merit_price: int, tang_kinh_level: int) -> int:
    base = ceil(max(0, int(merit_price)) / SCROLL_MERIT_PER_CH)
    discounted = ceil(base * (1.0 - scroll_discount(tang_kinh_level)))
    return max(1, discounted)


def scroll_slots(tang_kinh_level: int) -> list[SectShopSlot]:
    cap = scroll_grade_cap(tang_kinh_level)
    if cap <= 0:
        return []
    from src.game.systems.economy import get_skill_scroll_shop
    return [
        SectShopSlot(
            item_key=s.item_key,
            grade=s.grade,
            price_ch=scroll_price_ch(s.price, tang_kinh_level),
            weekly_limit=0,
            section="scroll",
        )
        for s in get_skill_scroll_shop()
        if s.grade <= cap
    ]


def full_catalog(
    sect_id: int, week_key: str, facility_levels: dict[str, int]
) -> list[SectShopSlot]:
    """The authoritative shelf for a sect this week — the cog both renders
    this and re-derives it at purchase time (never trust a client-cached
    price)."""
    return (
        fixed_slots()
        + rotating_slots(sect_id, week_key, facility_levels.get("tu_bao_cac", 0))
        + scroll_slots(facility_levels.get("tang_kinh_cac", 0))
    )


def find_slot(
    catalog: list[SectShopSlot], item_key: str, grade: int
) -> SectShopSlot | None:
    for slot in catalog:
        if slot.item_key == item_key and slot.grade == int(grade):
            return slot
    return None
