"""Equipment stat computation — converts equipped items into combat stat bonuses."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.db.models.item_instance import ItemInstance

# Human-readable labels for stat keys
STAT_LABELS: dict[str, str] = {
    "atk":             "Công",
    "matk":            "Pháp Công",
    "def_stat":        "Phòng",
    "hp_max":          "HP",
    "mp_max":          "MP",
    "crit_rating":     "Tỉ Lệ Bạo",
    "crit_dmg_rating": "Bạo Thương",
    "evasion_rating":  "Né Tránh",
    "crit_res_rating": "Kháng Bạo",
    "accuracy_rating": "Chuẩn Xác",
    "final_dmg_bonus": "Tăng ST",
    "final_dmg_reduce":"Giảm ST",
    "hp_regen_pct":    "Hồi HP %",
    "hp_regen_flat":   "Hồi HP",
    "mp_regen_pct":    "Hồi MP %",
    "mp_regen_flat":   "Hồi MP",
    "res_all":         "Kháng TN",
    "element_dmg_all":   "ST Nguyên Tố",
    "element_dmg_kim":   "ST Kim",
    "element_dmg_moc":   "ST Mộc",
    "element_dmg_thuy":  "ST Thủy",
    "element_dmg_hoa":   "ST Hỏa",
    "element_dmg_tho":   "ST Thổ",
    "element_dmg_loi":   "ST Lôi",
    "element_dmg_phong": "ST Phong",
    "element_dmg_quang": "ST Quang",
    "element_dmg_am":    "ST Ám",
    "res_kim":   "Kháng Kim",
    "res_moc":   "Kháng Mộc",
    "res_thuy":  "Kháng Thủy",
    "res_hoa":   "Kháng Hỏa",
    "res_tho":   "Kháng Thổ",
    "res_loi":   "Kháng Lôi",
    "res_phong": "Kháng Phong",
    "res_quang": "Kháng Quang",
    "res_am":    "Kháng Ám",
    # Per-element max-resist cap lifters — raise the player's soft cap
    # (75%) toward the hard cap (90%). See effects.effective_res_cap.
    "kim_max_resist_bonus":   "Cap Kháng Kim",
    "moc_max_resist_bonus":   "Cap Kháng Mộc",
    "thuy_max_resist_bonus":  "Cap Kháng Thủy",
    "hoa_max_resist_bonus":   "Cap Kháng Hỏa",
    "tho_max_resist_bonus":   "Cap Kháng Thổ",
    "loi_max_resist_bonus":   "Cap Kháng Lôi",
    "phong_max_resist_bonus": "Cap Kháng Phong",
    "quang_max_resist_bonus": "Cap Kháng Quang",
    "am_max_resist_bonus":    "Cap Kháng Ám",
    # Energy Shield stats — see Combatant.shield_cap() for the formula.
    "shield_max_base":   "Khiên Nền",
    "shield_max_flat":   "Khiên Tối Đa",
    "shield_max_pct":    "Khiên Tối Đa %",
    "shield_regen_flat": "Hồi Khiên",
    "shield_regen_pct":  "Hồi Khiên %",
    # DOT bonuses (rolled by weapon/glove/ring/amulet affixes).
    "burn_dmg_bonus":    "ST Thiêu Đốt",
    "bleed_dmg_bonus":   "ST Chảy Máu",
    "poison_dmg_bonus":  "ST Trúng Độc",
    "dot_dmg_bonus":     "ST DOT",
    # Other affix-only stats.
    "thorn_pct":                    "Phản Đòn",
    "damage_bonus_from_shield_pct": "ST từ Khiên",
    "spd_bonus":                    "Tốc Độ",
    # Super-material grant stats (forge.py stamps these as ``type="super"``
    # affixes; bool flags surface in inspect view via the equipment cog).
    "heal_pct":              "Trị Liệu",
    "bleed_on_hit_pct":      "Tỉ Lệ Chảy Máu",
    "bleed_heal_reduce":     "Giảm Hồi Khi Chảy Máu",
    "silence_on_crit_pct":   "Cấm Phép (Bạo Kích)",
    "heal_can_crit":         "Trị Liệu Có Thể Bạo Kích",
    "barrier_on_cleanse":    "Thanh Tẩy Tạo Khiên",
}

SLOT_LABELS: dict[str, str] = {
    "weapon":   "⚔️ Vũ Khí",
    "off_hand": "🛡️ Phụ Khí",
    "armor":    "🥋 Giáp",
    "helmet":   "🪖 Mũ",
    "glove":    "🧤 Găng",
    "belt":     "👑 Đai",
    "boot":     "👢 Giày",
    "ring":     "💍 Nhẫn",
    "amulet":   "📿 Bội",
}

SLOT_ORDER = ("weapon", "off_hand", "armor", "helmet", "glove", "belt", "boot", "ring", "amulet")

_PCT_STATS = frozenset({
    "final_dmg_bonus", "final_dmg_reduce",
    "hp_regen_pct", "mp_regen_pct",
    "res_all",
    "element_dmg_all",
    "element_dmg_kim", "element_dmg_moc", "element_dmg_thuy", "element_dmg_hoa",
    "element_dmg_tho", "element_dmg_loi", "element_dmg_phong",
    "element_dmg_quang", "element_dmg_am",
    "res_kim", "res_moc", "res_thuy", "res_hoa", "res_tho",
    "res_loi", "res_phong", "res_quang", "res_am",
    "kim_max_resist_bonus", "moc_max_resist_bonus", "thuy_max_resist_bonus",
    "hoa_max_resist_bonus", "tho_max_resist_bonus", "loi_max_resist_bonus",
    "phong_max_resist_bonus", "quang_max_resist_bonus", "am_max_resist_bonus",
    "shield_max_pct", "shield_regen_pct",
    # DOT-bonus & misc-pct affix stats — rolled as fractions (0.06 = 6%).
    # Without this, format_stat fell through to the int branch and rendered
    # rolled values as "+0 burn_dmg_bonus" etc. on every dropped/forged item.
    "burn_dmg_bonus", "bleed_dmg_bonus", "poison_dmg_bonus", "dot_dmg_bonus",
    "thorn_pct", "damage_bonus_from_shield_pct",
    # Super-material grant stats — see STAT_LABELS additions for context.
    "heal_pct", "bleed_on_hit_pct", "bleed_heal_reduce", "silence_on_crit_pct",
})


def compute_equipment_stats(equipped: list["ItemInstance"]) -> dict[str, float]:
    """Sum all stat bonuses from a player's equipped ItemInstances.

    Each ItemInstance has a pre-computed `computed_stats` dict that already
    includes both implicit base stats and rolled affix values.

    Unique items may also declare a ``passive_bonus`` dict in their JSON
    definition — this is layered on top of the summed numeric stats so unique
    gear can grant on-hit/on-crit procs and immunity flags that go beyond
    simple stat sheets.

    Forged items may additionally carry a ``super_material_key`` referencing
    a super-rare forge material whose ``granted_passive`` dict is merged on
    top using the same bool-safe rules.
    """
    from src.data.registry import registry

    def _merge_passive(container: dict, passive: dict) -> None:
        """Merge ``passive`` into ``container``. Numbers add, bools OR,
        nested dicts merge recursively (per-sub-key, same rules). Lets a
        unique JSON declare ``passive_bonus: { dot_dmg_bonus_by_kind:
        { burn: 0.20 } }`` and have it land in ``totals`` as a nested dict
        that the denester in build_combat_stats then flattens."""
        for stat, val in passive.items():
            if isinstance(val, bool):
                container[stat] = bool(container.get(stat)) or val
            elif isinstance(val, dict):
                existing = container.setdefault(stat, {})
                if isinstance(existing, dict):
                    _merge_passive(existing, val)
                else:
                    container[stat] = dict(val)
            else:
                container[stat] = container.get(stat, 0.0) + float(val)

    totals: dict[str, float] = {}
    for inst in equipped:
        if inst.location != "equipped":
            continue
        for stat, val in (inst.computed_stats or {}).items():
            totals[stat] = totals.get(stat, 0.0) + float(val)

        # Merge unique passive bonuses if the item is a unique with passives
        uniq_key = getattr(inst, "unique_key", None)
        if uniq_key:
            uniq_def = registry.get_unique(uniq_key) or {}
            _merge_passive(totals, uniq_def.get("passive_bonus") or {})

        # Super-material grants — numeric portions are already baked into
        # the item's ``computed_stats`` as ``type="super"`` affixes (forge.py),
        # so we only need to merge bool flags here. Skipping numerics avoids
        # double-counting; merging bools (e.g. ``barrier_on_cleanse``,
        # ``heal_can_crit``) keeps the flag-style passives wired up since
        # ``compute_stats`` only sums numerics and can't carry booleans.
        super_key = getattr(inst, "super_material_key", None)
        if super_key:
            super_def = registry.get_super_material(super_key) or {}
            bool_only = {
                k: v for k, v in (super_def.get("granted_passive") or {}).items()
                if isinstance(v, bool)
            }
            if bool_only:
                _merge_passive(totals, bool_only)
    return totals


def format_stat(key: str, val: float) -> str:
    """Format a single stat value for display."""
    label = STAT_LABELS.get(key, key)
    if key in _PCT_STATS:
        return f"+{val * 100:.1f}% {label}"
    return f"+{int(val)} {label}"


def format_computed_stats(computed_stats: dict) -> str:
    """Format a computed_stats dict as a compact inline string."""
    parts = [format_stat(k, v) for k, v in computed_stats.items()]
    return " | ".join(parts) if parts else "—"
