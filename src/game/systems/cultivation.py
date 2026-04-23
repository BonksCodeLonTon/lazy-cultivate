"""Cultivation system — manages 3-axis progression."""
from __future__ import annotations

from src.game.constants.balance import (
    AXIS_ATK_WEIGHT, AXIS_DEF_WEIGHT, AXIS_HP_WEIGHT, AXIS_MATK_WEIGHT, AXIS_MP_WEIGHT,
    BASE_ATK_PER_LEVEL, BASE_DEF_PER_LEVEL, BASE_HP_PER_LEVEL,
    BASE_MATK_PER_LEVEL, BASE_MP_PER_LEVEL,
)
from src.game.constants.currencies import (
    FORMATION_MERIT_COST_BASE,
    TURNS_PER_CULT_LEVEL,
)
from src.game.constants.realms import (
    BODY_REALMS,
    QI_REALMS,
    FORMATION_REALMS,
    LEVELS_PER_REALM,
    MERIT_TO_FORMATION_EXP_RATIO,
    TRIBULATION_EXP_COST,
    get_level_from_exp
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


def compute_atk(character: Character, bonuses: dict | None = None) -> int:
    """Physical attack — Luyện Thể (body) axis contributes 60%."""
    body_stages = character.body_realm * LEVELS_PER_REALM + character.body_level
    qi_stages = character.qi_realm * LEVELS_PER_REALM + character.qi_level
    form_stages = character.formation_realm * LEVELS_PER_REALM + character.formation_level

    base = (
        body_stages * BASE_ATK_PER_LEVEL * AXIS_ATK_WEIGHT["body"]
        + qi_stages * BASE_ATK_PER_LEVEL * AXIS_ATK_WEIGHT["qi"]
        + form_stages * BASE_ATK_PER_LEVEL * AXIS_ATK_WEIGHT["formation"]
    )
    result = max(1, int(base))
    if bonuses:
        result += int(bonuses.get("atk_bonus", 0))
        result = int(result * (1.0 + bonuses.get("atk_pct", 0.0)))
    return max(1, result)


def compute_matk(character: Character, bonuses: dict | None = None) -> int:
    """Magic attack — Luyện Khí (qi) axis contributes 60%."""
    body_stages = character.body_realm * LEVELS_PER_REALM + character.body_level
    qi_stages = character.qi_realm * LEVELS_PER_REALM + character.qi_level
    form_stages = character.formation_realm * LEVELS_PER_REALM + character.formation_level

    base = (
        body_stages * BASE_MATK_PER_LEVEL * AXIS_MATK_WEIGHT["body"]
        + qi_stages * BASE_MATK_PER_LEVEL * AXIS_MATK_WEIGHT["qi"]
        + form_stages * BASE_MATK_PER_LEVEL * AXIS_MATK_WEIGHT["formation"]
    )
    result = max(1, int(base))
    if bonuses:
        result += int(bonuses.get("matk_bonus", 0))
        result = int(result * (1.0 + bonuses.get("matk_pct", 0.0)))
    return max(1, result)


def compute_def_stat(character: Character, bonuses: dict | None = None) -> int:
    """Physical defense — Luyện Thể (body) axis contributes 55%."""
    body_stages = character.body_realm * LEVELS_PER_REALM + character.body_level
    qi_stages = character.qi_realm * LEVELS_PER_REALM + character.qi_level
    form_stages = character.formation_realm * LEVELS_PER_REALM + character.formation_level

    base = (
        body_stages * BASE_DEF_PER_LEVEL * AXIS_DEF_WEIGHT["body"]
        + qi_stages * BASE_DEF_PER_LEVEL * AXIS_DEF_WEIGHT["qi"]
        + form_stages * BASE_DEF_PER_LEVEL * AXIS_DEF_WEIGHT["formation"]
    )
    result = max(0, int(base))
    if bonuses:
        result += int(bonuses.get("def_bonus", 0))
        result = int(result * (1.0 + bonuses.get("def_pct", 0.0)))
    return max(0, result)


def compute_gem_bonuses(gem_keys: list[str]) -> dict:
    """Merge per-gem elemental bonuses from a list of inlaid gem keys.

    Each gem key format: ``Gem<Element>_<grade>`` (e.g. ``GemKim_2``, ``GemHoa_4``).
    Bonus = GEM_ELEMENT_BASE_BONUS[element] × grade, summed across all gems.
    """
    from src.game.constants.balance import GEM_ELEMENT_BASE_BONUS
    merged: dict = {}
    if not gem_keys:
        return merged

    # Element aliases: gem key uses capitalised segment that must map to element key
    elem_map = {
        "Kim": "kim", "Moc": "moc", "Thuy": "thuy", "Hoa": "hoa",
        "Tho": "tho", "Loi": "loi", "Phong": "phong", "Quang": "quang", "Am": "am",
    }

    for key in gem_keys:
        if not key or not key.startswith("Gem"):
            continue
        body = key[3:]  # strip "Gem" prefix
        # Split element vs grade: "Kim_2" → ("Kim", "2")
        if "_" in body:
            elem_part, grade_part = body.split("_", 1)
            try:
                grade = int(grade_part)
            except ValueError:
                grade = 1
        else:
            elem_part, grade = body, 1
        elem = elem_map.get(elem_part)
        if not elem:
            continue
        base = GEM_ELEMENT_BASE_BONUS.get(elem, {})
        for stat, value in base.items():
            merged[stat] = merged.get(stat, 0) + value * grade
    return merged


def compute_formation_reserve_pct(formation_key: str | None, gem_count: int) -> float:
    """How much of max MP the active formation locks.

    0 if no formation active; else BASE + PER_GEM × gem_count, capped at MAX.
    """
    if not formation_key:
        return 0.0
    from src.game.constants.balance import (
        FORMATION_BASE_RESERVE_PCT,
        FORMATION_GEM_RESERVE_PCT,
        FORMATION_MAX_RESERVE_PCT,
    )
    raw = FORMATION_BASE_RESERVE_PCT + max(0, gem_count) * FORMATION_GEM_RESERVE_PCT
    return min(FORMATION_MAX_RESERVE_PCT, raw)


def compute_formation_bonuses(
    formation_key: str | None,
    gem_count: int = 0,
    gem_keys: list[str] | None = None,
) -> dict:
    """Returns merged bonuses from a formation's base stats + reached gem thresholds
    + per-gem elemental bonuses (when ``gem_keys`` is provided).

    Also populates:
      ``_formation_element``  — for res_element routing
      ``_mp_reserve_pct``     — fraction of max MP the formation reserves
    """
    if not formation_key:
        return {}
    from src.data.registry import registry
    form_data = registry.get_formation(formation_key)
    if not form_data:
        return {}

    # Use explicit gem_keys when provided; otherwise rely on gem_count only
    effective_count = len(gem_keys) if gem_keys is not None else gem_count

    merged: dict = {}
    _merge_bonus_dict(merged, form_data.get("base_stat_bonus", {}))

    thresholds = form_data.get("gem_threshold_bonuses", {})
    for threshold_str, bonus in sorted(thresholds.items(), key=lambda x: int(x[0])):
        if effective_count >= int(threshold_str):
            _merge_bonus_dict(merged, bonus)

    # Per-gem elemental bonuses (only when gem list is known)
    if gem_keys:
        _merge_bonus_dict(merged, compute_gem_bonuses(gem_keys))

    # Store the formation element so callers can apply res_element to the right slot
    if "element" in form_data and form_data["element"]:
        merged["_formation_element"] = form_data["element"]

    merged["_mp_reserve_pct"] = compute_formation_reserve_pct(formation_key, effective_count)
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


def can_breakthrough(character: Character, axis: str) -> tuple[bool, str]:
    xp_attr = f"{axis}_xp"
    current_xp = getattr(character, xp_attr)
    
    if current_xp < TRIBULATION_EXP_COST:
        needed = TRIBULATION_EXP_COST - current_xp
        return False, f"Linh khí chưa đủ tụ (Thiếu {needed:,} EXP để Độ Kiếp)."
    
    return True, "Cảm nhận thiên địa dị động, Thiên Kiếp chuẩn bị giáng xuống!"


def consume_breakthrough_costs(
    character: Character,
    axis: str,
    inventory: dict[str, int] | None = None,
) -> None:
    realm = getattr(character, f"{axis}_realm") if axis != "formation" else character.formation_realm
    reqs = get_breakthrough_requirements(axis, realm)

    if axis == "formation":
        character.merit -= reqs["merit_cost"]
    else:
        if inventory is not None and reqs["item_key"]:
            inventory[reqs["item_key"]] = inventory.get(reqs["item_key"], 0) - reqs["quantity"]

def apply_realm_up(character: Character, axis: str) -> None:
    if axis == "body":
        character.body_realm += 1
        character.body_level = 1
        character.body_xp = 0
        if character.body_realm == len(BODY_REALMS) - 1 and not character.dao_ti_unlocked:
            character.dao_ti_unlocked = True
    elif axis == "qi":
        character.qi_realm += 1
        character.qi_level = 1
        character.qi_xp = 0
    elif axis == "formation":
        character.formation_realm += 1
        character.formation_level = 1
        character.formation_xp = 0


def apply_breakthrough(
    character: Character,
    axis: str,
    inventory: dict[str, int] | None = None,
) -> None:
    
    realm_idx = getattr(character, f"{axis}_realm")
    reqs = get_breakthrough_requirements(axis, realm_idx)

    if axis in ["body", "qi"]:
        if inventory is not None and reqs.get("item_key"):
            item_key = reqs["item_key"]
            inventory[item_key] = inventory.get(item_key, 0) - reqs["quantity"]
            
    elif axis == "formation":
        character.merit -= reqs.get("merit_cost", 0)

    new_realm = realm_idx + 1
    setattr(character, f"{axis}_realm", new_realm)
    setattr(character, f"{axis}_level", 1)
    setattr(character, f"{axis}_xp", 0)

    if axis == "body" and new_realm == len(BODY_REALMS) - 1:
        if hasattr(character, "dao_ti_unlocked"):
            character.dao_ti_unlocked = True


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


def advance_cultivation_xp(character: Character, turns: int) -> dict:
    axis = character.active_axis
    
    realms_map = {
        "qi": QI_REALMS,
        "body": BODY_REALMS,
        "formation": FORMATION_REALMS
    }
    r_list = realms_map.get(axis, QI_REALMS)
    realm_idx = getattr(character, f"{axis}_realm")
    realm = r_list[realm_idx]
    
    exp_gained = turns * realm.base_exp_rate
    
    xp_attr = f"{axis}_xp"
    current_xp = getattr(character, xp_attr)
    new_total_xp = current_xp + exp_gained
    setattr(character, xp_attr, new_total_xp)
    
    level_attr = f"{axis}_level" if axis != "formation" else "formation_level"
    old_level = getattr(character, level_attr)
    new_level = get_level_from_exp(new_total_xp)
    setattr(character, level_attr, new_level)
    
    return {
        "axis": axis,
        "exp_gained": exp_gained,
        "current_total_xp": new_total_xp,
        "levels_gained": max(0, new_level - old_level),
        "is_ready_for_tribulation": new_total_xp >= TRIBULATION_EXP_COST
    }

def study_formation_with_merit(character: Character, merits: int) -> dict:
    """
    Hàm mới: Tu luyện Trận Đạo bằng cách tiêu tốn Công Đức.
    Dùng cho yêu cầu: 'Trận pháp nâng cấp bằng công đức'.
    """
    if merits <= 0:
        return {"success": False, "error": "Số lượng Công Đức không hợp lệ."}
        
    if character.merit < merits:
        return {"success": False, "error": f"Không đủ Công Đức (Cần {merits}, hiện có {character.merit})"}
    
    exp_gained = merits * MERIT_TO_FORMATION_EXP_RATIO
    
    character.merit -= merits
    character.formation_xp += exp_gained
    
    new_level = get_level_from_exp(character.formation_xp)
    character.formation_level = new_level
    
    return {
        "success": True,
        "merit_spent": merits,
        "exp_gained": exp_gained,
        "current_total_xp": character.formation_xp,
        "current_level": new_level
    }