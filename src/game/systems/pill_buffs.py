"""Permanent combat-buff pill counters.

A handful of pill ``effect_key``s (``buff_speed``, ``buff_def``,
``buff_sword_dmg``, ``buff_element_<elem>``) used to be no-ops. They now
grant *permanent* per-pill stat increments, capped per-key so a player
can't infinitely stack the same buff. Each player carries a
JSON-encoded counter dict (``Player.pill_buff_counts``) — consume_pill
increments the relevant key, ``compute_combat_stats`` reads the count
and adds ``count × per_pill_increment`` to the matching stat.

This module is the single source of truth for:
  * Which effect_keys are "combat buffs" (and therefore capped).
  * The per-pill increment for each buff key.
  * Parse/encode helpers for the JSON column.
"""
from __future__ import annotations

import json


# Per-key consumption cap. Pills past this count refuse to apply (so the
# player can spend them on a different category instead of wasting). 20
# pills × the per-pill values below ≈ +10 SPD / +200 DEF / +100 ATK /
# +20 % per-element damage at full saturation.
PILL_BUFF_CAP: int = 20


# Effect key → {stat_name: per_pill_amount}. Stat names are the same keys
# the combat-stats pipeline already knows about (``spd``, ``def_stat``,
# ``atk``) plus the per-element damage dict (``element_dmg_bonus.<elem>``
# expressed below as ``element_dmg_bonus_<elem>`` for flat-key storage —
# the apply step parses the suffix).
PILL_BUFF_STATS: dict[str, dict[str, float]] = {
    "buff_speed":         {"spd":            0.5},
    "buff_def":           {"def_stat":       10.0},
    "buff_sword_dmg":     {"atk":             5.0},
    "buff_element_kim":   {"element_dmg_bonus_kim":   0.01},
    "buff_element_moc":   {"element_dmg_bonus_moc":   0.01},
    "buff_element_thuy":  {"element_dmg_bonus_thuy":  0.01},
    "buff_element_hoa":   {"element_dmg_bonus_hoa":   0.01},
    "buff_element_tho":   {"element_dmg_bonus_tho":   0.01},
    "buff_element_loi":   {"element_dmg_bonus_loi":   0.01},
    "buff_element_phong": {"element_dmg_bonus_phong": 0.01},
    "buff_element_quang": {"element_dmg_bonus_quang": 0.01},
    "buff_element_am":    {"element_dmg_bonus_am":    0.01},
}

# New heaven-treasure pill effects: these four keys map to the Thiên-tier
# heaventreasure items. Values are per-pill increments; at PILL_BUFF_CAP
# (20) the cumulative bonuses approximately match the design table.
PILL_BUFF_STATS.update({
    "pill_linh_moc": {
        "hp_pct": 0.01,            # ×20 → +20% HP
        "hp_regen_pct": 0.0025,    # ×20 → +5% HP/turn
        "res_all": 0.004,          # ×20 → +8% elemental res
    },
    "pill_dark_ice": {
        "hp_pct": 0.01,            # ×20 → +20% HP
        "shield_max_pct": 0.006,   # ×20 → +12% shield cap
        "hp_regen_pct": 0.0025,    # ×20 → +5% HP/turn
    },
    "pill_divine_thunder": {
        "matk_pct": 0.0075,        # ×20 → +15% MATK
        "spd_pct": 0.007,          # ×20 → +14% SPD
        "crit_rating": 5.0,        # ×20 → +100 crit rating
    },
    "pill_wind_spirit": {
        "spd_pct": 0.009,          # ×20 → +18% SPD
        "evasion_rating": 11.0,    # ×20 → +220 evasion
        "crit_rating": 5.0,        # ×20 → +100 crit rating
    },
})


def parse_counts(raw: str | None) -> dict[str, int]:
    """Decode the stored JSON counter dict. Tolerates None / malformed."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, (int, float))}


def encode_counts(counts: dict[str, int]) -> str:
    """Encode counters → JSON string for storage. Empty dict → ``"{}"``."""
    return json.dumps(counts, separators=(",", ":"))


def is_buff_pill(effect_key: str | None) -> bool:
    """True if the pill is one of the cap-tracked permanent combat buffs."""
    return effect_key in PILL_BUFF_STATS


def increment_count(
    counts: dict[str, int], effect_key: str,
) -> tuple[bool, dict[str, int]]:
    """Try to increment ``counts[effect_key]``. Returns ``(ok, new_counts)``.

    ``ok=False`` means the cap was already reached and nothing changed.
    Always returns a fresh dict (immutable mutation pattern).
    """
    current = int(counts.get(effect_key, 0))
    if current >= PILL_BUFF_CAP:
        return False, dict(counts)
    new_counts = dict(counts)
    new_counts[effect_key] = current + 1
    return True, new_counts


def total_buff_for_stat(
    counts: dict[str, int], stat_name: str,
) -> float:
    """Sum the cumulative buff for ``stat_name`` across every pill effect."""
    total = 0.0
    for effect_key, count in counts.items():
        cap = PILL_BUFF_CAP
        applied = min(int(count), cap)
        per_pill = PILL_BUFF_STATS.get(effect_key, {}).get(stat_name)
        if per_pill is None:
            continue
        total += applied * per_pill
    return total
