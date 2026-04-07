"""Cultivation system — manages 3-axis progression."""
from __future__ import annotations

from src.game.constants.currencies import (
    FORMATION_MERIT_COST_BASE,
    TURNS_PER_CULT_LEVEL,
)
from src.game.constants.realms import (
    BODY_REALMS,
    QI_REALMS,
    FORMATION_REALMS,
    LEVELS_PER_REALM,
)
from src.game.models.character import Character


# ── Breakthrough material requirements ────────────────────────────────────────
# Maps current_realm_index → (item_key, quantity_required)

BODY_BREAKTHROUGH_MATERIALS: dict[int, tuple[str, int]] = {
    0: ("MatHuyetTinh",     1),  # Luyện Huyết → Luyện Bì
    1: ("MatBiPhach",       1),  # Luyện Bì    → Luyện Cân
    2: ("MatCanNguyen",     2),  # Luyện Cân   → Luyện Cốt
    3: ("MatCotHoa",        2),  # Luyện Cốt   → Luyện Phủ
    4: ("MatNguTang",       3),  # Luyện Phủ   → Pháp Tướng
    5: ("MatPhaTuongKinh",  3),  # Pháp Tướng  → Kim Thân
    6: ("MatKimThanDan",    5),  # Kim Thân    → Siêu Phàm
    7: ("MatSieuPhamTinh",  5),  # Siêu Phàm   → Nhập Thánh
}

QI_BREAKTHROUGH_MATERIALS: dict[int, tuple[str, int]] = {
    0: ("MatKhiTuDan",      1),  # Luyện Khí  → Trúc Cơ
    1: ("MatTrucCoDan",     1),  # Trúc Cơ    → Kim Đan
    2: ("MatKimDanNguyen",  2),  # Kim Đan    → Nguyên Anh
    3: ("MatNguyenAnhThach",2),  # Nguyên Anh → Hóa Thần
    4: ("MatHoaThanDan",    3),  # Hóa Thần   → Luyện Hư
    5: ("MatHuKhongTinh",   3),  # Luyện Hư   → Hợp Đạo
    6: ("MatHopDaoNgoc",    5),  # Hợp Đạo   → Đại Thừa
    7: ("MatDaiThuaKinh",   5),  # Đại Thừa  → Đăng Tiên
}

# Formation breakthrough costs Công Đức only (no materials per design doc)
FORMATION_BREAKTHROUGH_MERIT: dict[int, int] = {
    0:  10_000,
    1:  20_000,
    2:  40_000,
    3:  60_000,
    4: 100_000,
    5: 150_000,
    6: 250_000,
    7: 500_000,
}


# HP/MP distribution per axis (% of total stats)
# Luyện Thể: 50% HP, 16% MP
# Luyện Khí:  34% HP, 31% MP
# Trận Đạo:  16% HP, 52% MP
AXIS_HP_WEIGHT = {"body": 0.50, "qi": 0.34, "formation": 0.16}
AXIS_MP_WEIGHT = {"body": 0.16, "qi": 0.31, "formation": 0.52}

BASE_HP_PER_LEVEL = 500
BASE_MP_PER_LEVEL = 150


def compute_hp_max(character: Character, bonuses: dict | None = None) -> int:
    body_stages = character.body_realm * LEVELS_PER_REALM + character.body_level
    qi_stages = character.qi_realm * LEVELS_PER_REALM + character.qi_level
    form_stages = character.formation_realm * LEVELS_PER_REALM + character.formation_level

    base = (
        body_stages * BASE_HP_PER_LEVEL * AXIS_HP_WEIGHT["body"]
        + qi_stages * BASE_HP_PER_LEVEL * AXIS_HP_WEIGHT["qi"]
        + form_stages * BASE_HP_PER_LEVEL * AXIS_HP_WEIGHT["formation"]
    )
    result = max(1, int(base))
    if bonuses:
        result = int(result * (1.0 + bonuses.get("hp_pct", 0.0)))
    return max(1, result)


def compute_mp_max(character: Character, bonuses: dict | None = None) -> int:
    body_stages = character.body_realm * LEVELS_PER_REALM + character.body_level
    qi_stages = character.qi_realm * LEVELS_PER_REALM + character.qi_level
    form_stages = character.formation_realm * LEVELS_PER_REALM + character.formation_level

    base = (
        body_stages * BASE_MP_PER_LEVEL * AXIS_MP_WEIGHT["body"]
        + qi_stages * BASE_MP_PER_LEVEL * AXIS_MP_WEIGHT["qi"]
        + form_stages * BASE_MP_PER_LEVEL * AXIS_MP_WEIGHT["formation"]
    )
    result = max(1, int(base))
    if bonuses:
        result = int(result * (1.0 + bonuses.get("mp_pct", 0.0)))
    return max(1, result)


def compute_formation_bonuses(formation_key: str | None, gem_count: int) -> dict:
    """Returns merged bonuses from a formation's base stats + all reached gem thresholds."""
    if not formation_key:
        return {}
    from src.data.registry import registry
    form_data = registry.get_formation(formation_key)
    if not form_data:
        return {}

    merged: dict = {}
    _merge_bonus_dict(merged, form_data.get("base_stat_bonus", {}))

    thresholds = form_data.get("gem_threshold_bonuses", {})
    for threshold_str, bonus in sorted(thresholds.items(), key=lambda x: int(x[0])):
        if gem_count >= int(threshold_str):
            _merge_bonus_dict(merged, bonus)

    # Store the formation element so callers can apply res_element to the right slot
    if "element" in form_data and form_data["element"]:
        merged["_formation_element"] = form_data["element"]

    return merged


def compute_constitution_bonuses(constitution_type: str) -> dict:
    """Returns stat_bonuses dict for the given constitution key."""
    if not constitution_type:
        return {}
    from src.data.registry import registry
    const_data = registry.get_constitution(constitution_type)
    if not const_data:
        return {}
    return dict(const_data.get("stat_bonuses", {}))


def merge_bonuses(*dicts: dict) -> dict:
    """Additively merge multiple bonus dicts into one."""
    merged: dict = {}
    for d in dicts:
        _merge_bonus_dict(merged, d)
    return merged


def _merge_bonus_dict(target: dict, source: dict) -> None:
    for k, v in source.items():
        if k == "note":
            continue
        if isinstance(v, bool):
            target[k] = v  # bool flags (poison_immunity etc.) — last-write wins
        elif isinstance(v, (int, float)):
            target[k] = target.get(k, type(v)(0)) + v
        else:
            target[k] = v


def get_breakthrough_requirements(axis: str, realm: int) -> dict:
    """Return what is needed to break through from *realm* to realm+1.

    Returns a dict with keys:
      - "item_key"   (str | None)
      - "quantity"   (int)
      - "merit_cost" (int)
    """
    if axis == "body":
        mat = BODY_BREAKTHROUGH_MATERIALS.get(realm)
        return {
            "item_key":   mat[0] if mat else None,
            "quantity":   mat[1] if mat else 0,
            "merit_cost": 0,
        }
    if axis == "qi":
        mat = QI_BREAKTHROUGH_MATERIALS.get(realm)
        return {
            "item_key":   mat[0] if mat else None,
            "quantity":   mat[1] if mat else 0,
            "merit_cost": 0,
        }
    if axis == "formation":
        return {
            "item_key":   None,
            "quantity":   0,
            "merit_cost": FORMATION_BREAKTHROUGH_MERIT.get(realm, 0),
        }
    return {"item_key": None, "quantity": 0, "merit_cost": 0}


def can_breakthrough(
    character: Character,
    axis: str,
    inventory: dict[str, int] | None = None,
) -> tuple[bool, str]:
    """Check if *character* can attempt a realm breakthrough on *axis*.

    *inventory* is an optional mapping of ``{item_key: quantity}`` from the
    player's inventory.  When omitted, material checks are skipped (useful for
    display-only queries).
    """
    if axis == "body":
        realm, level = character.body_realm, character.body_level
        realms = BODY_REALMS
    elif axis == "qi":
        realm, level = character.qi_realm, character.qi_level
        realms = QI_REALMS
    elif axis == "formation":
        realm, level = character.formation_realm, character.formation_level
        realms = FORMATION_REALMS
    else:
        return False, "Trục tu luyện không hợp lệ."

    if level < LEVELS_PER_REALM:
        return False, f"Cần đạt Cấp {LEVELS_PER_REALM} trước khi đột phá."
    if realm >= len(realms) - 1:
        return False, "Đã đạt cảnh giới tối cao của trục này."

    reqs = get_breakthrough_requirements(axis, realm)

    # Material check (body / qi)
    if reqs["item_key"] and inventory is not None:
        have = inventory.get(reqs["item_key"], 0)
        if have < reqs["quantity"]:
            from src.data.registry import registry
            item_data = registry.get_item(reqs["item_key"])
            item_name = item_data["vi"] if item_data else reqs["item_key"]
            return (
                False,
                f"Cần **{reqs['quantity']}× {item_name}** để đột phá "
                f"(hiện có: {have}).",
            )

    # Merit check (formation)
    if reqs["merit_cost"] > 0:
        if character.merit < reqs["merit_cost"]:
            return (
                False,
                f"Cần **{reqs['merit_cost']:,} Công Đức** để đột phá Trận Đạo "
                f"(hiện có: {character.merit:,}).",
            )

    return True, ""


def apply_breakthrough(
    character: Character,
    axis: str,
    inventory: dict[str, int] | None = None,
) -> None:
    """Advance *character* to the next realm on *axis* and consume costs.

    Mutates *character* (realm, level, merit) and *inventory* in-place.
    Assumes ``can_breakthrough`` has already been called successfully.
    """
    realm: int
    if axis == "body":
        realm = character.body_realm
        reqs  = get_breakthrough_requirements("body", realm)
        if inventory is not None and reqs["item_key"]:
            inventory[reqs["item_key"]] = inventory.get(reqs["item_key"], 0) - reqs["quantity"]
        character.body_realm += 1
        character.body_level  = 1
        character.body_xp     = 0
        if character.body_realm == len(BODY_REALMS) - 1 and not character.dao_ti_unlocked:
            character.dao_ti_unlocked = True

    elif axis == "qi":
        realm = character.qi_realm
        reqs  = get_breakthrough_requirements("qi", realm)
        if inventory is not None and reqs["item_key"]:
            inventory[reqs["item_key"]] = inventory.get(reqs["item_key"], 0) - reqs["quantity"]
        character.qi_realm += 1
        character.qi_level  = 1
        character.qi_xp     = 0

    elif axis == "formation":
        realm = character.formation_realm
        reqs  = get_breakthrough_requirements("formation", realm)
        character.merit -= reqs["merit_cost"]
        character.formation_realm += 1
        character.formation_level  = 1
        character.formation_xp     = 0


def advance_cultivation_xp(character: Character, turns: int) -> dict:
    """Apply *turns* of cultivation XP to *character.active_axis*.

    Levels up automatically when XP threshold is reached, stopping at
    level 9 (manual breakthrough required to advance realm).

    For Trận Đạo: deducts Công Đức per level; stops leveling if merit runs out.

    Returns a summary dict:
      levels_gained, merit_spent, blocked_by_merit (bool), blocked_at_level_9 (bool)
    """
    axis = character.active_axis
    turns_per_level = TURNS_PER_CULT_LEVEL[axis]

    xp_attr    = f"{axis}_xp"
    level_attr = f"{axis}_level" if axis != "formation" else "formation_level"
    realm_attr = f"{axis}_realm" if axis != "formation" else "formation_realm"

    xp    = getattr(character, xp_attr)
    level = getattr(character, level_attr)
    realm = getattr(character, realm_attr)

    xp += turns
    levels_gained    = 0
    merit_spent      = 0
    blocked_by_merit = False

    while xp >= turns_per_level and level < LEVELS_PER_REALM:
        # Formation costs Công Đức per level
        if axis == "formation":
            cost = FORMATION_MERIT_COST_BASE * (realm + 1)
            if character.merit < cost:
                blocked_by_merit = True
                break
            character.merit -= cost
            merit_spent += cost

        xp    -= turns_per_level
        level += 1
        levels_gained += 1

    # Cap XP at threshold when stuck at level 9 (avoid infinite accumulation)
    if level >= LEVELS_PER_REALM:
        xp = min(xp, turns_per_level - 1)

    setattr(character, xp_attr,    xp)
    setattr(character, level_attr, level)

    return {
        "levels_gained":    levels_gained,
        "merit_spent":      merit_spent,
        "blocked_by_merit": blocked_by_merit,
        "blocked_at_9":     level >= LEVELS_PER_REALM,
    }
