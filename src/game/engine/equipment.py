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
    "final_dmg_bonus": "Tăng ST",
    "final_dmg_reduce":"Giảm ST",
    "hp_regen_pct":    "Hồi HP %",
    "hp_regen_flat":   "Hồi HP",
    "mp_regen_pct":    "Hồi MP %",
    "mp_regen_flat":   "Hồi MP",
    "res_all":         "Kháng TN",
}

SLOT_LABELS: dict[str, str] = {
    "weapon":   "⚔️ Vũ Khí",
    "off_hand": "🛡️ Phụ Khí",
    "armor":    "🥋 Giáp",
    "helmet":   "🪖 Mũ",
    "glove":    "🧤 Găng",
    "belt":     "👑 Đai",
    "ring":     "💍 Nhẫn",
    "amulet":   "📿 Bội",
}

SLOT_ORDER = ("weapon", "off_hand", "armor", "helmet", "glove", "belt", "ring", "amulet")

_PCT_STATS = frozenset({
    "final_dmg_bonus", "final_dmg_reduce",
    "hp_regen_pct", "mp_regen_pct",
    "res_all",
})


def compute_equipment_stats(equipped: list["ItemInstance"]) -> dict[str, float]:
    """Sum all stat bonuses from a player's equipped ItemInstances.

    Each ItemInstance has a pre-computed `computed_stats` dict that already
    includes both implicit base stats and rolled affix values.
    """
    totals: dict[str, float] = {}
    for inst in equipped:
        if inst.location != "equipped":
            continue
        for stat, val in (inst.computed_stats or {}).items():
            totals[stat] = totals.get(stat, 0.0) + float(val)
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
