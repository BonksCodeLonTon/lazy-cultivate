"""Linh Căn system — unlock and upgrade per-element progression.

Reads ``Player.linh_can`` (the comma-separated storage column), validates
material costs from the registry, and enforces the qi-realm cap.

Cap rule: per-element level ≤ ``qi_realm + 1`` (Luyện Khí caps at lv1,
Đăng Tiên at lv9). See ``max_linh_can_level``.

Cost model (kept inside this module so balance lives in one place):

    UPGRADE
      • 1 dedicated level material (LCKim3, LCMoc7, …)
      • catalysts scaling with the target level (Vạn Linh / Thiên Địa / …)
      • merit cost scaling with target level

    UNLOCK
      • 1 element-specific unlock material (LCUnlockKim …)
      • 1 Hồng Mông Khải Linh Châu when unlocking the 4th+ Linh Căn
      • merit cost scaling with how many Linh Căn the player already owns
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data.registry import registry
from src.game.constants.linh_can import (
    LINH_CAN_DATA, LINH_CAN_MAX_LEVEL, LINH_CAN_MIN_LEVEL,
    format_linh_can_levels, max_linh_can_level, parse_linh_can_levels,
)


# ── Cost tables ──────────────────────────────────────────────────────────────

# Catalysts required for each *upgrade target level*. Keys are item keys; the
# value is the quantity consumed.
_UPGRADE_CATALYSTS: dict[int, dict[str, int]] = {
    2: {"LCCatTinhNguyenChuLinh": 1},
    3: {"LCCatTinhNguyenChuLinh": 2, "LCCatVanLinhDan": 1},
    4: {"LCCatVanLinhDan": 2},
    5: {"LCCatVanLinhDan": 3, "LCCatThienDiaTinhHoa": 1},
    6: {"LCCatThienDiaTinhHoa": 2, "LCCatLinhKhiHoiTu": 1},
    7: {"LCCatThienDiaTinhHoa": 2, "LCCatLinhHonCongSinh": 1, "LCCatTuPhuDangLinh": 1},
    8: {"LCCatLinhHonCongSinh": 2, "LCCatTuPhuDangLinh": 2, "LCCatVoCucLinhDinh": 1},
    9: {"LCCatTuPhuDangLinh": 3, "LCCatCuuLinhThongTuy": 1, "LCCatVoCucLinhDinh": 2},
}

_UPGRADE_MERIT_COST: dict[int, int] = {
    2:    20_000,
    3:    50_000,
    4:   120_000,
    5:   280_000,
    6:   600_000,
    7: 1_200_000,
    8: 2_400_000,
    9: 5_000_000,
}

_UNLOCK_BASE_MERIT = 100_000
_UNLOCK_MERIT_PER_EXISTING = 50_000  # each Linh Căn already owned adds this
_UNLOCK_FOURTH_PLUS_CATALYST = "LCCatHongMongKhaiLinh"


# ── Public types ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LinhCanCost:
    """Material/merit requirement for one unlock or upgrade operation."""
    materials: dict[str, int]   # item_key → quantity
    merit: int

    def is_free(self) -> bool:
        return not self.materials and self.merit == 0


@dataclass(frozen=True)
class LinhCanError(Exception):
    """Validation failure surfaced by unlock/upgrade. Discord layer renders ``message``."""
    message: str

    def __str__(self) -> str:
        return self.message


# ── Read helpers ─────────────────────────────────────────────────────────────

def get_levels(player) -> dict[str, int]:
    """Return a fresh ``{element: level}`` dict for the player's Linh Căn."""
    return parse_linh_can_levels(getattr(player, "linh_can", "") or "")


def get_level(player, element: str) -> int:
    """Return the player's current level for one element (0 = not unlocked)."""
    return get_levels(player).get(element, 0)


def player_max_level(player) -> int:
    """Per-element level cap based on the player's qi_realm."""
    return max_linh_can_level(int(getattr(player, "qi_realm", 0)))


# ── Cost lookups ─────────────────────────────────────────────────────────────

def upgrade_cost(element: str, target_level: int) -> LinhCanCost:
    """Materials + merit needed to take ``element`` from ``target_level - 1`` → ``target_level``.

    Raises ``LinhCanError`` if the target level is out of range or no
    upgrade material exists for it.
    """
    if element not in LINH_CAN_DATA:
        raise LinhCanError(f"Linh Căn không hợp lệ: `{element}`")
    if not (LINH_CAN_MIN_LEVEL < target_level <= LINH_CAN_MAX_LEVEL):
        raise LinhCanError(
            f"Cấp Linh Căn nâng phải nằm trong khoảng "
            f"{LINH_CAN_MIN_LEVEL + 1}–{LINH_CAN_MAX_LEVEL}."
        )

    mats = registry.linh_can_materials_for(element, "upgrade", target_level)
    if not mats:
        raise LinhCanError(
            f"Thiếu nguyên liệu nâng Lv{target_level} cho `{element}` trong dữ liệu."
        )

    materials: dict[str, int] = {mats[0]["key"]: 1}
    for cat_key, qty in _UPGRADE_CATALYSTS.get(target_level, {}).items():
        materials[cat_key] = materials.get(cat_key, 0) + qty

    return LinhCanCost(materials=materials, merit=_UPGRADE_MERIT_COST.get(target_level, 0))


def unlock_cost(element: str, existing_count: int) -> LinhCanCost:
    """Materials + merit needed to unlock ``element``.

    ``existing_count`` is how many Linh Căn the player already owns; opening
    a 4th+ root requires the Hồng Mông Khải Linh Châu catalyst.
    """
    if element not in LINH_CAN_DATA:
        raise LinhCanError(f"Linh Căn không hợp lệ: `{element}`")

    mats = registry.linh_can_materials_for(element, "unlock")
    if not mats:
        raise LinhCanError(f"Thiếu nguyên liệu khai mở `{element}` trong dữ liệu.")

    materials: dict[str, int] = {mats[0]["key"]: 1}
    if existing_count >= 3:
        materials[_UNLOCK_FOURTH_PLUS_CATALYST] = materials.get(
            _UNLOCK_FOURTH_PLUS_CATALYST, 0,
        ) + 1

    merit = _UNLOCK_BASE_MERIT + existing_count * _UNLOCK_MERIT_PER_EXISTING
    return LinhCanCost(materials=materials, merit=merit)


# ── Mutation helpers ─────────────────────────────────────────────────────────

async def unlock_linh_can(session, player, element: str) -> dict:
    """Unlock a new Linh Căn for the player, consuming materials + merit.

    Returns a summary dict ``{"element": ..., "level": 1, "spent_merit": ...,
    "consumed": {...}}``. Raises ``LinhCanError`` on validation failure.
    """
    levels = get_levels(player)
    if element in levels:
        raise LinhCanError(f"Bạn đã sở hữu Linh Căn **{LINH_CAN_DATA[element]['vi']}** rồi.")

    cost = unlock_cost(element, existing_count=len(levels))
    await _consume(session, player, cost)

    levels[element] = LINH_CAN_MIN_LEVEL
    player.linh_can = format_linh_can_levels(levels)
    return {
        "element": element,
        "level": LINH_CAN_MIN_LEVEL,
        "spent_merit": cost.merit,
        "consumed": dict(cost.materials),
    }


async def upgrade_linh_can(session, player, element: str) -> dict:
    """Raise the player's level in ``element`` by 1, consuming materials + merit.

    Caps at the player's qi_realm-derived limit. Raises ``LinhCanError`` if
    the element isn't unlocked, the cap is hit, or the player can't afford.
    """
    levels = get_levels(player)
    if element not in levels:
        raise LinhCanError(
            f"Bạn chưa khai mở Linh Căn **{LINH_CAN_DATA[element]['vi']}**. "
            f"Dùng `/linh_can_unlock` trước."
        )

    current = levels[element]
    cap = player_max_level(player)
    if current >= cap:
        raise LinhCanError(
            f"Linh Căn **{LINH_CAN_DATA[element]['vi']}** đã đạt giới hạn Lv{cap} "
            f"theo cảnh giới Luyện Khí hiện tại. Đột phá để mở Lv tiếp theo."
        )

    target = current + 1
    cost = upgrade_cost(element, target)
    await _consume(session, player, cost)

    levels[element] = target
    player.linh_can = format_linh_can_levels(levels)
    return {
        "element": element,
        "level": target,
        "spent_merit": cost.merit,
        "consumed": dict(cost.materials),
    }


# ── Internal: deduct materials + merit atomically inside the caller's session ──

async def _consume(session, player, cost: LinhCanCost) -> None:
    """Deduct merit and materials. Raises ``LinhCanError`` if anything is short.

    Uses ``InventoryRepository`` so it lives inside the same transaction as
    the player mutation.
    """
    from src.db.repositories.inventory_repo import InventoryRepository
    from src.game.constants.grades import Grade

    if player.merit < cost.merit:
        raise LinhCanError(
            f"Thiếu Công Đức: cần **{cost.merit:,}**, hiện có **{player.merit:,}**."
        )

    irepo = InventoryRepository(session)
    # Pre-flight: check every material before consuming any so we don't half-consume.
    for item_key, qty in cost.materials.items():
        item = registry.get_item(item_key)
        if not item:
            raise LinhCanError(f"Nguyên liệu `{item_key}` không tồn tại trong dữ liệu.")
        grade = Grade(int(item.get("grade", 1)))
        if not await irepo.has_item(player.id, item_key, grade, qty):
            raise LinhCanError(
                f"Thiếu **{item['vi']}** ×{qty} (cần đủ trong túi đồ trước khi nâng)."
            )

    # Commit deductions.
    player.merit -= cost.merit
    for item_key, qty in cost.materials.items():
        item = registry.get_item(item_key)
        grade = Grade(int(item.get("grade", 1)))
        await irepo.remove_item(player.id, item_key, grade, qty)
