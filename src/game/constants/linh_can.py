"""Linh Căn (Spiritual Root) constants and helpers.

Each Linh Căn an player owns has its own progression level (1..LINH_CAN_MAX_LEVEL).
The level is hard-capped by the player's qi axis realm — see
``max_linh_can_level``. Storage format on ``Player.linh_can`` is a
comma-separated list, with optional ``:level`` suffix per element:

    "kim,hoa"        → legacy, treated as level 1 for both
    "kim:3,hoa:2"    → kim at lv3, hoa at lv2

All readers should use ``parse_linh_can_levels`` to get the level dict and
``parse_linh_can`` to get the bare list of element keys.
"""
from __future__ import annotations

# All possible Linh Căn keys
ALL_LINH_CAN = ["kim", "moc", "thuy", "hoa", "tho", "phong", "loi", "quang", "am"]

LINH_CAN_MIN_LEVEL = 1
LINH_CAN_MAX_LEVEL = 9


# Per-element scaling rules consumed by ``compute_linh_can_bonuses``.
#
# ``passive_bonus_per_level``
#     Linear multiplier applied to every numeric ``passive_bonus`` value:
#     ``value * (1 + (level - 1) * mult)``. Default 0.5 → level 9 = 5× lv1.
#
# ``proc_chance_per_level``
#     Flat add to base ``proc_chance`` per level above 1. The effect modules
#     read this via ``scaled_proc_chance(actor, element)``.
#
# ``thresholds``
#     ``{level: {"add_passive": {...}, "label": "..."}}``. Each entry's
#     ``add_passive`` dict is folded into the bonus map once the player's
#     level reaches that threshold. Use this to grant brand-new effects
#     (extra crit, new on-hit proc, immunity flag, etc.) at milestones.
LINH_CAN_DATA: dict[str, dict] = {
    "kim": {
        "vi": "Kim",
        "emoji": "⚙️",
        "description": "+5% sát thương",
        "passive_bonus": {"final_dmg_bonus": 0.05},
        "combat_effect": "xuyen_thau",
        "proc_chance": 0.20,
        "effect_desc": "Xuyên Thấu (20%): Bỏ qua 30% giáp mục tiêu",
        "scaling": {
            "passive_bonus_per_level": 0.50,
            "proc_chance_per_level": 0.015,
            "thresholds": {
                3: {"add_passive": {"true_dmg_pct": 0.04},
                    "label": "Lv3 — Khắc Cốt: +4% sát thương xuyên giáp"},
                5: {"add_passive": {"crit_rating": 80},
                    "label": "Lv5 — Phá Khí: +80 bạo kích"},
                7: {"add_passive": {"bleed_on_hit_pct": 0.10},
                    "label": "Lv7 — Huyết Sát: 10% gây Chảy Máu khi đánh"},
                9: {"add_passive": {"final_dmg_bonus": 0.15, "true_dmg_pct": 0.06},
                    "label": "Lv9 — Kim Thân Bất Hoại: +15% sát thương cuối, +6% xuyên giáp"},
            },
        },
    },
    "moc": {
        "vi": "Mộc",
        "emoji": "🌿",
        "description": "+8% Sinh Lực tối đa",
        "passive_bonus": {"hp_pct": 0.08},
        "combat_effect": "hoi_xuan",
        "proc_chance": 1.0,
        "effect_desc": "Hồi Xuân (100%): Cuối lượt hồi 4% HP tối đa",
        "scaling": {
            "passive_bonus_per_level": 0.45,
            "proc_chance_per_level": 0.0,
            "thresholds": {
                3: {"add_passive": {"hp_regen_pct": 0.01},
                    "label": "Lv3 — Sinh Cơ: +1% HP/lượt"},
                5: {"add_passive": {"heal_pct": 0.10},
                    "label": "Lv5 — Linh Khí Tái Sinh: +10% hiệu quả trị liệu"},
                7: {"add_passive": {"dot_leech_pct": 0.10},
                    "label": "Lv7 — Diệp Lạc Quy Căn: 10% hút máu từ DoT"},
                9: {"add_passive": {"hp_pct": 0.20, "heal_can_crit": True},
                    "label": "Lv9 — Trường Sinh Mộc Cốt: +20% HP, hồi máu có thể bạo kích"},
            },
        },
    },
    "thuy": {
        "vi": "Thủy",
        "emoji": "💧",
        "description": "+5% giảm sát thương nhận vào",
        "passive_bonus": {"final_dmg_reduce": 0.05},
        "combat_effect": "ngung_dong",
        "proc_chance": 0.15,
        "effect_desc": "Ngưng Đọng (15%): Giảm 25% tốc đối thủ 2 lượt",
        "scaling": {
            "passive_bonus_per_level": 0.50,
            "proc_chance_per_level": 0.015,
            "thresholds": {
                3: {"add_passive": {"mp_regen_pct": 0.02},
                    "label": "Lv3 — Thuỷ Triều: +2% MP/lượt"},
                5: {"add_passive": {"reflect_pct": 0.08},
                    "label": "Lv5 — Băng Kính: phản 8% sát thương"},
                7: {"add_passive": {"slow_on_hit_pct": 0.10},
                    "label": "Lv7 — Hàn Tủy: +10% gây Chậm khi đánh"},
                9: {"add_passive": {"final_dmg_reduce": 0.10, "reflect_applies_effects": True},
                    "label": "Lv9 — Vô Cực Thuỷ Tâm: +10% giảm sát thương, phản truyền debuff"},
            },
        },
    },
    "hoa": {
        "vi": "Hỏa",
        "emoji": "🔥",
        "description": "+5% sát thương",
        "passive_bonus": {"final_dmg_bonus": 0.05},
        "combat_effect": "bao_liet",
        "proc_chance": 0.20,
        "effect_desc": "Bạo Liệt (20%): Thiêu Đốt 4% HP/lượt × 2 lượt",
        "scaling": {
            "passive_bonus_per_level": 0.50,
            "proc_chance_per_level": 0.015,
            "thresholds": {
                3: {"add_passive": {"burn_on_hit_pct": 0.10},
                    "label": "Lv3 — Diễm Trảo: +10% gây Thiêu Đốt khi đánh"},
                5: {"add_passive": {"burn_dmg_bonus": 0.15},
                    "label": "Lv5 — Liệt Hỏa: +15% sát thương Thiêu Đốt"},
                7: {"add_passive": {"fire_res_shred": 0.15, "burn_stack_cap_bonus": 1},
                    "label": "Lv7 — Phần Thiên: −15% kháng Hỏa, +1 stack Thiêu Đốt"},
                9: {"add_passive": {"final_dmg_bonus": 0.15, "dot_can_crit": True},
                    "label": "Lv9 — Thái Dương Đan Tâm: +15% sát thương cuối, DoT có thể bạo kích"},
            },
        },
    },
    "tho": {
        "vi": "Thổ",
        "emoji": "🪨",
        "description": "+8% giảm sát thương nhận vào",
        "passive_bonus": {"final_dmg_reduce": 0.08},
        "combat_effect": "ho_the",
        "proc_chance": 1.0,
        "effect_desc": "Hộ Thể (khi HP<35%): Khiên 20% HP tối đa (1 lần/trận)",
        "scaling": {
            "passive_bonus_per_level": 0.40,
            "proc_chance_per_level": 0.0,
            "thresholds": {
                3: {"add_passive": {"shield_regen_pct": 0.05},
                    "label": "Lv3 — Cương Phách: hồi 5% khiên/lượt"},
                5: {"add_passive": {"thorn_pct": 0.08},
                    "label": "Lv5 — Phản Phệ Thuẫn: 8% phản sát thương"},
                7: {"add_passive": {"damage_bonus_from_shield_pct": 0.20, "shield_cap_pct_bonus": 0.20},
                    "label": "Lv7 — Hậu Thổ: +20% sát thương dựa trên khiên, +20% trần khiên"},
                9: {"add_passive": {"final_dmg_reduce": 0.10, "thorn_from_shield": True, "stun_on_hit_pct": 0.05},
                    "label": "Lv9 — Bàn Cổ Thổ Cốt: +10% giảm sát thương, phản từ khiên, 5% choáng"},
            },
        },
    },
    "phong": {
        "vi": "Phong",
        "emoji": "🌪️",
        "description": "+8% tốc độ",
        "passive_bonus": {"spd_pct": 0.08},
        "combat_effect": "toc_bien",
        "proc_chance": 0.10,
        "effect_desc": "Tốc Biến (10%): Né tránh hoàn toàn đòn tấn công",
        "scaling": {
            "passive_bonus_per_level": 0.45,
            "proc_chance_per_level": 0.01,
            "thresholds": {
                3: {"add_passive": {"evasion_rating": 80},
                    "label": "Lv3 — Lưu Phong: +80 né tránh"},
                5: {"add_passive": {"mark_on_hit_pct": 0.12},
                    "label": "Lv5 — Truy Hồn: 12% đánh dấu mục tiêu"},
                7: {"add_passive": {"damage_bonus_from_evasion_pct": 0.20},
                    "label": "Lv7 — Phong Thần: +20% sát thương dựa trên né"},
                9: {"add_passive": {"spd_pct": 0.15, "crit_rating_vs_marked": 120, "phong_res_shred": 0.15},
                    "label": "Lv9 — Bàn Cổ Linh Phong: +15% tốc, +120 bạo lên mục tiêu đánh dấu, −15% kháng Phong"},
            },
        },
    },
    "loi": {
        "vi": "Lôi",
        "emoji": "⚡",
        "description": "+5% tỷ lệ bạo kích",
        "passive_bonus": {"crit_rating": 65},
        "combat_effect": "te_liet",
        "proc_chance": 0.12,
        "effect_desc": "Tê Liệt (12% khi bạo kích): Choáng đối thủ 1 lượt",
        "scaling": {
            "passive_bonus_per_level": 0.50,
            "proc_chance_per_level": 0.015,
            "thresholds": {
                3: {"add_passive": {"shock_on_hit_pct": 0.10},
                    "label": "Lv3 — Tử Điện: +10% gây Sốc khi đánh"},
                5: {"add_passive": {"crit_dmg_rating": 100},
                    "label": "Lv5 — Lôi Đình: +100 sát thương bạo kích"},
                7: {"add_passive": {"loi_res_shred": 0.15, "shock_stack_cap_bonus": 1},
                    "label": "Lv7 — Phá Vân: −15% kháng Lôi, +1 stack Sốc"},
                9: {"add_passive": {"crit_rating": 150, "turn_steal_pct": 0.10},
                    "label": "Lv9 — Thái Sơ Lôi Tinh: +150 bạo, 10% cướp lượt"},
            },
        },
    },
    "quang": {
        "vi": "Quang",
        "emoji": "✨",
        "description": "+10% hiệu quả trị liệu",
        "passive_bonus": {"heal_pct": 0.10},
        "combat_effect": "thanh_tay",
        "proc_chance": 0.15,
        "effect_desc": "Thanh Tẩy (15%): Giải 1 hiệu ứng xấu + hồi 5% MP",
        "scaling": {
            "passive_bonus_per_level": 0.45,
            "proc_chance_per_level": 0.015,
            "thresholds": {
                3: {"add_passive": {"cleanse_on_turn_pct": 0.05},
                    "label": "Lv3 — Tẩy Tâm: +5% Thanh Tẩy"},
                5: {"add_passive": {"barrier_on_cleanse": True},
                    "label": "Lv5 — Hộ Thuẫn: Thanh Tẩy tạo khiên dựa trên MATK"},
                7: {"add_passive": {"silence_on_crit_pct": 0.12, "heal_reduce_on_hit_pct": 0.15},
                    "label": "Lv7 — Tịnh Thổ: 12% câm khi bạo, 15% giảm trị liệu địch"},
                9: {"add_passive": {"heal_pct": 0.20, "heal_can_crit": True, "quang_res_shred": 0.15},
                    "label": "Lv9 — Đại La Quang Đan: +20% trị liệu, hồi máu có thể bạo, −15% kháng Quang"},
            },
        },
    },
    "am": {
        "vi": "Ám",
        "emoji": "🌑",
        "description": "+5% né tránh",
        "passive_bonus": {"evasion_rating": 68},
        "combat_effect": "hu_the",
        "proc_chance": 0.15,
        "effect_desc": "Hủ Thể (15%): Hút máu 30% sát thương gây ra",
        "scaling": {
            "passive_bonus_per_level": 0.50,
            "proc_chance_per_level": 0.015,
            "thresholds": {
                3: {"add_passive": {"soul_drain_on_hit_pct": 0.10},
                    "label": "Lv3 — Phệ Hồn: +10% Hút Hồn"},
                5: {"add_passive": {"stat_steal_on_hit_pct": 0.08},
                    "label": "Lv5 — Đoạt Linh: +8% Cướp Chỉ Số"},
                7: {"add_passive": {"crit_rating_vs_drained": 100, "am_res_shred": 0.15},
                    "label": "Lv7 — U Minh: +100 bạo lên mục tiêu bị Phệ Hồn, −15% kháng Ám"},
                9: {"add_passive": {"evasion_rating": 120, "true_dmg_pct": 0.05},
                    "label": "Lv9 — Hư Vô Âm Đỉnh: +120 né, +5% sát thương xuyên giáp"},
            },
        },
    },
}


# ── Storage helpers ─────────────────────────────────────────────────────────

def parse_linh_can_levels(raw: str) -> dict[str, int]:
    """Parse comma-separated linh_can string into ``{element: level}`` dict.

    Accepts both the legacy ``"kim,hoa"`` (each treated as level 1) and the
    new ``"kim:3,hoa:2"`` format. Unknown elements are dropped silently.
    Levels are clamped to ``[LINH_CAN_MIN_LEVEL, LINH_CAN_MAX_LEVEL]``.
    """
    if not raw:
        return {}
    out: dict[str, int] = {}
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            elem, _, lvl_str = token.partition(":")
            elem = elem.strip()
            try:
                level = int(lvl_str.strip())
            except ValueError:
                level = LINH_CAN_MIN_LEVEL
        else:
            elem = token
            level = LINH_CAN_MIN_LEVEL
        if elem not in LINH_CAN_DATA:
            continue
        out[elem] = max(LINH_CAN_MIN_LEVEL, min(LINH_CAN_MAX_LEVEL, level))
    return out


def parse_linh_can(raw: str) -> list[str]:
    """Return just the list of element keys (no level info).

    Kept for backward compatibility with combat code that only checks
    membership (``"kim" in actor.linh_can``).
    """
    return list(parse_linh_can_levels(raw).keys())


def format_linh_can_levels(level_map: dict[str, int]) -> str:
    """Serialize a ``{element: level}`` dict into the new storage format.

    Stable ordering follows ``ALL_LINH_CAN`` so the same dict always
    produces the same string.
    """
    parts = []
    for elem in ALL_LINH_CAN:
        if elem in level_map:
            level = max(LINH_CAN_MIN_LEVEL, min(LINH_CAN_MAX_LEVEL, int(level_map[elem])))
            parts.append(f"{elem}:{level}")
    return ",".join(parts)


def format_linh_can(linh_can_list: list[str]) -> str:
    """Legacy: format a flat list of element keys (each at level 1)."""
    return format_linh_can_levels({elem: LINH_CAN_MIN_LEVEL for elem in linh_can_list})


def max_linh_can_level(qi_realm: int) -> int:
    """Per-element level cap for a given qi_realm (0-indexed).

    Linh Căn progression cannot exceed the player's Luyện Khí (qi axis)
    realm — Luyện Khí (realm 0) caps at lv1, Trúc Cơ at lv2, …, Đăng Tiên
    (realm 8) at lv9.
    """
    return max(LINH_CAN_MIN_LEVEL, min(LINH_CAN_MAX_LEVEL, qi_realm + 1))


# ── Bonus computation ──────────────────────────────────────────────────────

def _passive_bonus_for_element(element: str, level: int) -> dict:
    """Return scaled passive bonus dict for one element at the given level."""
    data = LINH_CAN_DATA.get(element, {})
    out: dict = {}

    scaling = data.get("scaling", {})
    per_level = float(scaling.get("passive_bonus_per_level", 0.0))
    multiplier = 1.0 + (max(1, level) - 1) * per_level

    for k, v in data.get("passive_bonus", {}).items():
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            scaled = v * multiplier
            out[k] = type(v)(scaled) if isinstance(v, int) else scaled
        else:
            out[k] = v

    # Threshold bonuses (granted once level reaches the threshold)
    for threshold, payload in scaling.get("thresholds", {}).items():
        if level >= int(threshold):
            for k, v in payload.get("add_passive", {}).items():
                if isinstance(v, bool):
                    out[k] = bool(out.get(k, False)) or v
                elif isinstance(v, (int, float)):
                    out[k] = out.get(k, type(v)(0)) + v
                else:
                    out[k] = v
    return out


def compute_linh_can_bonuses(linh_can: list[str] | dict[str, int]) -> dict:
    """Return merged passive bonus dict for the player's linh_can.

    Accepts either:
      - ``list[str]`` (legacy) — every element treated as level 1
      - ``dict[str, int]`` — explicit per-element levels

    Bonuses scale per element via ``_passive_bonus_for_element``.
    """
    if isinstance(linh_can, dict):
        level_map = {elem: int(lvl) for elem, lvl in linh_can.items()
                     if elem in LINH_CAN_DATA}
    else:
        level_map = {elem: LINH_CAN_MIN_LEVEL for elem in linh_can
                     if elem in LINH_CAN_DATA}

    merged: dict = {}
    for elem, level in level_map.items():
        for k, v in _passive_bonus_for_element(elem, level).items():
            if isinstance(v, bool):
                merged[k] = bool(merged.get(k, False)) or v
            elif isinstance(v, (int, float)):
                merged[k] = merged.get(k, type(v)(0)) + v
            else:
                merged[k] = v
    return merged


def scaled_proc_chance(level: int, base: float, per_level: float) -> float:
    """Helper for effect modules: scale a base proc chance by linh_can level."""
    bonus = max(0, level - 1) * per_level
    return min(1.0, max(0.0, base + bonus))


# ── Khí Tu breadth multiplier (the archetype's identity payoff) ────────────
#
# Mirror of Hỗn Độn Đạo Thể's ``all_passives_multiplier`` — but instead of
# rewarding 8 stacked Legendary constitutions, this rewards owning many
# high-level Linh Căn. Designed to give Khí Tu a payoff for breadth that
# Thể Tu (1 element + walls of HP) and Trận Tu (formation count, not Linh
# Căn) cannot easily replicate.
#
# Returns a multiplier ≥ 1.0 applied to the merged Linh Căn passive bonus
# dict. Curve:
#
#   • 0 high-level Linh Căn → ×1.00 (no synergy)
#   • Each Linh Căn at >= LINH_CAN_BREADTH_MIN_LEVEL adds + per-element step
#   • Cap at LINH_CAN_BREADTH_MAX_MULT to keep Phá Thiên + 9-element from
#     producing one-shot numbers (matches the rationale behind
#     ``cultivation._AMP_EXCLUDED_STATS``).
#
# At Lv7+ × 9 elements → cap. At 6 elements at Lv7+ → ~×1.40. The minimum
# threshold of Lv7 keeps the bonus inaccessible until the player has
# legitimately invested in materials per element, not just unlocked them.
LINH_CAN_BREADTH_MIN_LEVEL: int = 7
LINH_CAN_BREADTH_PER_ELEMENT: float = 0.10
LINH_CAN_BREADTH_MAX_MULT: float = 1.80


def linh_can_breadth_multiplier(levels: dict[str, int]) -> float:
    """Return the Khí-Tu breadth multiplier for the given linh_can map.

    Caller is responsible for the ``is_khi_tu`` archetype gate — this
    function only does the count math so it can also be used for previews
    in the status / linh_can hub embeds.
    """
    qualifying = sum(
        1 for lvl in levels.values()
        if int(lvl) >= LINH_CAN_BREADTH_MIN_LEVEL
    )
    raw = 1.0 + qualifying * LINH_CAN_BREADTH_PER_ELEMENT
    return min(LINH_CAN_BREADTH_MAX_MULT, raw)


def get_threshold_unlocks(element: str, level: int) -> list[str]:
    """Return labels of every threshold unlocked at or below ``level``."""
    data = LINH_CAN_DATA.get(element, {})
    scaling = data.get("scaling", {})
    out: list[str] = []
    for threshold, payload in sorted(scaling.get("thresholds", {}).items()):
        if level >= int(threshold):
            label = payload.get("label")
            if label:
                out.append(label)
    return out
