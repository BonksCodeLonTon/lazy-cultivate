"""Cultivation system — manages 3-axis progression."""
from __future__ import annotations

from src.game.constants.balance import (
    AXIS_ATK_WEIGHT, AXIS_DEF_WEIGHT, AXIS_HP_WEIGHT, AXIS_MATK_WEIGHT, AXIS_MP_WEIGHT,
    BASE_ATK_PER_LEVEL, BASE_DEF_PER_LEVEL, BASE_HP_PER_LEVEL,
    BASE_MATK_PER_LEVEL, BASE_MP_PER_LEVEL,
    FORMATION_PATH_MULT_PER_STAGE,
)
from src.game.constants.realms import (
    BODY_REALMS,
    QI_REALMS,
    FORMATION_REALMS,
    LEVELS_PER_REALM,
    MERIT_TO_FORMATION_EXP_RATIO,
    get_level_from_exp,
    get_realm,
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
    """Merge per-gem bonuses from a list of inlaid gem keys.

    Two gem flavours:

    1. Elemental gems (``Gem<Element>_<grade>``, e.g. ``GemKim_2``): flat
       stat bonus = ``GEM_ELEMENT_BASE_BONUS[element] × grade``, summed.
    2. Unique gems (``GemUnique_<Name>`` — loaded from
       ``items/unique_gems.json``): the registry entry carries an explicit
       ``unique_bonus`` dict with stats AND special-effect flags. The dict
       merges as-is (no grade multiplier — uniques are tuned directly).

    Stats across both flavours additively combine. Bool flags OR together.
    Special keys ``*_stack_cap_bonus`` merge additively; they're consumed by
    ``compute_combat_stats`` to bump the base stack cap of burn/bleed/shock/
    mana / shield without overwriting the floor.
    """
    from src.game.constants.balance import GEM_ELEMENT_BASE_BONUS
    from src.data.registry import registry
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

        # ── Unique gem path — stats + special effects from registry JSON ───
        item = registry.get_item(key)
        if item and item.get("unique"):
            for stat, value in (item.get("unique_bonus") or {}).items():
                if isinstance(value, bool):
                    merged[stat] = merged.get(stat, False) or value
                else:
                    merged[stat] = merged.get(stat, 0) + value
            continue

        # ── Standard elemental gem path ────────────────────────────────────
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


# ── Multi-formation helpers ─────────────────────────────────────────────────
# Mirrors ``the_chat.get_constitutions`` / ``set_constitutions``. Player
# ``active_formation`` column is a comma-separated list of formation keys in
# slot order; single-entry values (legacy "CuuCungBatQua") parse as a 1-slot
# list unchanged.

MAX_FORMATION_SLOT_CEILING: int = 3


def get_active_formations(raw: str | None) -> list[str]:
    """Parse ``player.active_formation`` column → slot-ordered list of keys."""
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def set_active_formations(keys: list[str]) -> str | None:
    """Serialize a slot list back to the column; empty list → None for DB null."""
    joined = ",".join(k for k in keys if k)
    return joined or None


def is_tran_tu(body_realm: int, qi_realm: int, formation_realm: int) -> bool:
    """Trận Tu = formation path dominant — formation_realm ≥ every other axis."""
    return formation_realm >= max(body_realm, qi_realm)


def is_khi_tu(body_realm: int, qi_realm: int, formation_realm: int) -> bool:
    """Khí Tu = qi axis dominant.

    Mirror of ``is_the_tu`` / ``is_tran_tu``: the qi realm must be strictly
    greater than every other axis. The strict inequality (vs Trận Tu's
    ``>=``) keeps tied 0/0/0 starters out of the archetype until the
    player commits to qi. Used to gate the Linh Căn breadth multiplier so
    body-leaning players can't dip into the bonus by maxing qi later.
    """
    return qi_realm > max(body_realm, formation_realm)


def max_formation_slots(body_realm: int, qi_realm: int, formation_realm: int) -> int:
    """How many formations the player can run simultaneously.

    - Non-Trận-Tu: 1 slot (the classic single-formation behavior).
    - Trận Tu: unlocks extra slots as Trận Đạo progresses, capped at
      ``MAX_FORMATION_SLOT_CEILING`` (3) — matches the design intent where the
      50 % MP-reservation ceiling becomes load-bearing because multiple
      formations actually stack.

    Slot curve (Trận Tu):
        formation_realm 0-2 → 1 slot
        formation_realm 3-5 → 2 slots
        formation_realm 6-8 → 3 slots
    """
    if not is_tran_tu(body_realm, qi_realm, formation_realm):
        return 1
    return min(MAX_FORMATION_SLOT_CEILING, 1 + formation_realm // 3)


def formation_reserve_reduction(formation_stages: int) -> float:
    """Return the MULTIPLIER applied to MP reservation cost based on Trận Đạo progression.

    1.0 = full cost; lower values = less MP locked. Trận Đạo cultivators
    learn to fold the formation into less of their qi reserve over time.
    At 1 stage  → ~0.99 (almost full cost);
    at 81 stages → floored at FORMATION_RESERVE_FLOOR_MULT (default 0.30).
    """
    from src.game.constants.balance import (
        FORMATION_RESERVE_REDUCE_PER_STAGE,
        FORMATION_RESERVE_FLOOR_MULT,
    )
    raw = 1.0 - max(0, formation_stages) * FORMATION_RESERVE_REDUCE_PER_STAGE
    return max(FORMATION_RESERVE_FLOOR_MULT, raw)


def compute_formation_reserve_pct(
    formation_key: str | None,
    gem_count: int,
    formation_stages: int = 0,
) -> float:
    """How much of max MP an active formation slot locks via its inlaid gems.

    0 if no formation active; else (PER_GEM × gem_count) × reduction_mult,
    capped at MAX. The formation's *base* reservation now comes from the
    matching formation skill's ``reserved_mp_pct`` (see
    ``compute_formation_skill_reserve_pct``) — this function only accounts for
    the per-gem cost layered on top.
    """
    if not formation_key:
        return 0.0
    from src.game.constants.balance import (
        FORMATION_GEM_RESERVE_PCT,
        FORMATION_MAX_RESERVE_PCT,
    )
    raw = max(0, gem_count) * FORMATION_GEM_RESERVE_PCT
    reduced = raw * formation_reserve_reduction(formation_stages)
    return min(FORMATION_MAX_RESERVE_PCT, reduced)


def compute_formation_skill_reserve_pct(
    learned_skill_keys: list[str] | None,
    formation_stages: int = 0,
) -> float:
    """How much of max MP is locked by formation skills in the player's bar.

    Sums ``reserved_mp_pct`` across every formation-category skill currently
    equipped in a slot, scaled by Trận Đạo reduction and capped at MAX. This
    replaces the old flat ``FORMATION_BASE_RESERVE_PCT`` — bigger formations
    cost more MP to channel; smaller ones cost less.
    """
    if not learned_skill_keys:
        return 0.0
    from src.data.registry import registry
    from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
    total = 0.0
    for skill_key in learned_skill_keys:
        skill = registry.get_skill(skill_key)
        if not skill or skill.get("category") != "formation":
            continue
        total += float(skill.get("reserved_mp_pct", 0.0))
    reduced = total * formation_reserve_reduction(formation_stages)
    return min(FORMATION_MAX_RESERVE_PCT, reduced)


def formation_path_multiplier(formation_stages: int) -> float:
    """Return the multiplier applied to formation bonuses based on Trận Đạo progression.

    formation_stages = formation_realm * 9 + formation_level  (range 1..81).
    At 1 stage  → 1.02 (2% boost); at 81 stages → 2.62 (+162% boost).
    Designed so a late-game Trận Đạo cultivator's formations feel meaningfully
    stronger than a beginner's.
    """
    return 1.0 + max(0, formation_stages) * FORMATION_PATH_MULT_PER_STAGE


def compute_formation_bonuses(
    formation_key: str | None,
    gem_count: int = 0,
    gem_keys: list[str] | None = None,
    formation_stages: int = 0,
) -> dict:
    """Returns merged bonuses from a formation's base stats + reached gem thresholds
    + per-gem elemental bonuses (when ``gem_keys`` is provided).

    All numeric bonuses are scaled by ``formation_path_multiplier(formation_stages)``
    so progressing Trận Đạo makes the active formation genuinely stronger.

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

    # Scale all numeric bonuses by formation-path multiplier. Bool flags
    # (poison_immunity, freeze_on_skill, etc.) and meta fields stay untouched.
    mult = formation_path_multiplier(formation_stages)
    if mult != 1.0:
        for k, v in list(merged.items()):
            if isinstance(v, bool) or k.startswith("_") or k == "note":
                continue
            if isinstance(v, (int, float)):
                merged[k] = type(v)(v * mult) if isinstance(v, int) else v * mult

    # Store the formation element so callers can apply res_element to the right slot
    if "element" in form_data and form_data["element"]:
        merged["_formation_element"] = form_data["element"]

    merged["_mp_reserve_pct"] = compute_formation_reserve_pct(
        formation_key, effective_count, formation_stages=formation_stages,
    )
    merged["_formation_path_mult"] = mult
    return merged


def compute_formations_bonuses(
    formation_keys: list[str],
    gem_keys_by_formation: dict[str, list[str]] | None = None,
    formation_stages: int = 0,
) -> dict:
    """Multi-slot variant — sum bonuses from every active formation.

    Each entry in ``formation_keys`` contributes its own base + threshold +
    per-gem bonuses (via ``compute_formation_bonuses``). Numeric stats
    combine additively; meta fields resolve as follows:

        ``_mp_reserve_pct``        — sum across all active formations,
                                      capped at FORMATION_MAX_RESERVE_PCT
        ``_formation_element``     — first non-null element wins (for
                                      res_element routing); Trận Tu typically
                                      mixes elements across slots so this is
                                      best-effort
        ``_formation_path_mult``   — Trận Đạo progression is a per-player
                                      scalar, same across all slots

    ``gem_keys_by_formation`` maps ``formation_key → gem_key_list``. Missing
    entries default to 0 gems (no threshold bonuses, no elemental gem stats).
    """
    if not formation_keys:
        return {}
    from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT

    gem_map = gem_keys_by_formation or {}
    merged: dict = {}
    total_reserve = 0.0
    first_element: str | None = None
    path_mult_val: float | None = None

    for key in formation_keys:
        per = compute_formation_bonuses(
            key,
            gem_keys=gem_map.get(key),
            formation_stages=formation_stages,
        )
        if not per:
            continue
        # Pull meta fields out before merging stats — they aren't additive.
        total_reserve += float(per.pop("_mp_reserve_pct", 0.0))
        elem = per.pop("_formation_element", None)
        if first_element is None and elem:
            first_element = elem
        pm = per.pop("_formation_path_mult", None)
        if pm is not None:
            path_mult_val = pm
        # Remaining entries are real stats — additive merge.
        _merge_bonus_dict(merged, per)

    merged["_mp_reserve_pct"] = min(FORMATION_MAX_RESERVE_PCT, total_reserve)
    if first_element:
        merged["_formation_element"] = first_element
    if path_mult_val is not None:
        merged["_formation_path_mult"] = path_mult_val
    return merged


# Stats excluded from ``all_passives_multiplier`` amplification.
# These are the "compound-offensive" multipliers that already stack
# multiplicatively with every other damage source. Amplifying them on top of
# 8 stacked Legendary passives produces one-shot numbers vs world bosses
# (see tests/test_endgame_the_tu_vs_world_boss.py). Defensive multipliers
# (final_dmg_reduce) are excluded for the same reason — a Hỗn Độn carrier
# should feel legendary, not invincible.
_AMP_EXCLUDED_STATS: frozenset[str] = frozenset({
    "final_dmg_bonus",
    "true_dmg_pct",
    "cooldown_reduce",
    "final_dmg_reduce",
})


def compute_constitution_bonuses(constitution_type: str) -> dict:
    """Merge ``stat_bonuses`` from every equipped Thể Chất.

    ``constitution_type`` is a comma-separated list of keys (legacy
    single-key strings work unchanged). Bonuses from each equipped entry
    are additively combined.

    Special handling: any equipped Thể Chất that carries
    ``all_passives_multiplier > 0`` (currently only Hỗn Độn Đạo Thể) scales
    the numeric stat_bonuses from every *other* equipped entry by that
    factor. The carrier's own stats merge un-scaled — matching the lore
    "passives of the OTHER divine bodies are amplified". When multiple
    carriers are present, the highest multiplier wins. The control key
    itself is never merged as a stat so it can't leak into the merged dict.
    Bool flags (``dot_can_crit``, ``poison_immunity``, ...) are never scaled,
    and stats in ``_AMP_EXCLUDED_STATS`` merge at base — they compound
    multiplicatively elsewhere and amplifying them produced one-shot damage
    against world bosses.
    """
    if not constitution_type:
        return {}
    from src.data.registry import registry
    from src.game.systems.the_chat import get_constitutions

    keys = get_constitutions(constitution_type)

    # First pass — find the highest multiplier among equipped entries and
    # remember which constitution(s) provide it.
    mult = 1.0
    carriers: set[str] = set()
    for k in keys:
        c = registry.get_constitution(k) or {}
        raw = c.get("stat_bonuses", {}).get("all_passives_multiplier", 0)
        m = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
        if m > 0:
            carriers.add(k)
            if m > mult:
                mult = m

    # Second pass — merge each constitution's stats, scaling non-carrier
    # entries by `mult` when it's active.
    merged: dict = {}
    for k in keys:
        c = registry.get_constitution(k)
        if not c:
            continue
        scale = mult if (mult > 1.0 and k not in carriers) else 1.0
        for stat, val in (c.get("stat_bonuses") or {}).items():
            if stat == "all_passives_multiplier":
                continue   # control field — never a real stat
            # Compound-offensive multipliers merge at base (see _AMP_EXCLUDED_STATS).
            effective_scale = 1.0 if stat in _AMP_EXCLUDED_STATS else scale
            if effective_scale != 1.0 and isinstance(val, (int, float)) and not isinstance(val, bool):
                val = type(val)(val * effective_scale) if isinstance(val, int) else val * effective_scale
            _merge_bonus_dict(merged, {stat: val})

    return merged


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
    """Ready to break through once xp ≥ the current realm's bậc-9 threshold.

    ``inventory`` is accepted for call-site compatibility; material checks
    happen in ``apply_breakthrough``.
    """
    del inventory
    realm_idx = getattr(character, f"{axis}_realm")
    realm = get_realm(axis, realm_idx)
    if realm is None:
        return False, "Cảnh giới không hợp lệ."

    current_xp = getattr(character, f"{axis}_xp")
    max_xp = realm.level_exp_table[-1]
    if current_xp < max_xp:
        needed = max_xp - current_xp
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
    """Apply ``turns`` of cultivation to ``character.active_axis``.

    EXP earned = ``turns × realm.base_exp_rate`` on the current realm's table.
    Level (bậc 1..9) is derived from xp via the realm's level table.

    Formation has ``base_exp_rate == 0`` by design — Trận Đạo only advances
    through ``study_formation_with_merit`` (Công Đức → formation EXP).
    """
    axis = character.active_axis
    realm_idx = getattr(character, f"{axis}_realm")
    realm = get_realm(axis, realm_idx)

    if realm is None:
        return {
            "axis":                     axis,
            "exp_gained":               0,
            "current_total_xp":         getattr(character, f"{axis}_xp"),
            "levels_gained":            0,
            "is_ready_for_tribulation": False,
        }

    exp_gained = turns * realm.base_exp_rate

    xp_attr = f"{axis}_xp"
    current_xp = getattr(character, xp_attr)
    new_total_xp = current_xp + exp_gained
    setattr(character, xp_attr, new_total_xp)

    level_attr = f"{axis}_level"
    old_level = getattr(character, level_attr)
    new_level = get_level_from_exp(new_total_xp, realm)
    setattr(character, level_attr, new_level)

    return {
        "axis":                     axis,
        "exp_gained":               exp_gained,
        "current_total_xp":         new_total_xp,
        "levels_gained":            max(0, new_level - old_level),
        "is_ready_for_tribulation": new_total_xp >= realm.level_exp_table[-1],
    }

def study_formation_with_merit(character: Character, merits: int) -> dict:
    """Convert Công Đức into Trận Đạo EXP and re-derive bậc on the current
    formation realm's level table."""
    if merits <= 0:
        return {"success": False, "error": "Số lượng Công Đức không hợp lệ."}

    if character.merit < merits:
        return {"success": False, "error": f"Không đủ Công Đức (Cần {merits}, hiện có {character.merit})"}

    exp_gained = merits * MERIT_TO_FORMATION_EXP_RATIO
    character.merit -= merits
    character.formation_xp += exp_gained

    realm = get_realm("formation", character.formation_realm)
    character.formation_level = (
        get_level_from_exp(character.formation_xp, realm) if realm else 1
    )
    
    return {
        "success":          True,
        "merit_spent":      merits,
        "exp_gained":       exp_gained,
        "current_total_xp": character.formation_xp,
        "current_level":    character.formation_level,
    }