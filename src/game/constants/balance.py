"""Balance constants — every tunable number lives here.

Centralising these makes balancing straightforward: change a value once and
all systems that import from this module pick it up automatically.
Adding an equipment system is also simpler because equipment stat contributions
(e.g. +ATK from a weapon) can be compared against the same constants used to
calibrate cultivation growth.

Sections
--------
  Rating system       — crit/evasion/crit-res formula knobs
  Physical defense    — DEF-to-reduction formula
  Cultivation growth  — HP/MP/ATK/MATK/DEF per level and axis weights
  Realm power         — flat damage bonus per cultivation stage
  Enemy scaling       — HP/stat scaling formulas and rank defaults
  Player defaults     — baseline passive regen rates
"""
from __future__ import annotations

# ── Rating system ─────────────────────────────────────────────────────────────
# Formula: value = rating / (rating + RATING_K)
RATING_K: int = 1300          # denominator constant — higher = softer scaling

BASE_CRIT_CHANCE: float = 0.05   # 5 % base crit before crit_rating
MAX_CRIT_CHANCE: float = 0.75    # crit chance hard cap
BASE_CRIT_DMG_MULT: float = 1.5  # 150 % crit damage multiplier at 0 crit_dmg_rating
BASE_EVASION: float = 0.0        # 0 % base evasion before evasion_rating

# ── Physical defense ──────────────────────────────────────────────────────────
# Formula: reduction = min(MAX_PHYS_REDUCTION, def / (def + PHYS_DEF_K))
PHYS_DEF_K: float = 500.0        # 500 DEF → 50 % reduction
MAX_PHYS_REDUCTION: float = 0.75  # hard cap at 75 %

# ── Cultivation stat growth ───────────────────────────────────────────────────
# Base value gained per cultivation level across all three axes combined.
BASE_HP_PER_LEVEL: int = 500
BASE_MP_PER_LEVEL: int = 150
BASE_ATK_PER_LEVEL: int = 8    # Luyện Thể (body) path gives most ATK
BASE_MATK_PER_LEVEL: int = 8   # Luyện Khí (qi) path gives most MATK
BASE_DEF_PER_LEVEL: int = 4    # Luyện Thể (body) path gives most DEF

# Fraction of the total base-per-level value each axis contributes.
# Sum of each row must equal 1.0.
AXIS_HP_WEIGHT: dict[str, float] = {
    "body": 0.50, "qi": 0.34, "formation": 0.16,
}
AXIS_MP_WEIGHT: dict[str, float] = {
    "body": 0.16, "qi": 0.31, "formation": 0.52,
}
AXIS_ATK_WEIGHT: dict[str, float] = {
    "body": 0.60, "qi": 0.25, "formation": 0.15,
}
AXIS_MATK_WEIGHT: dict[str, float] = {
    "body": 0.15, "qi": 0.60, "formation": 0.25,
}
AXIS_DEF_WEIGHT: dict[str, float] = {
    "body": 0.55, "qi": 0.25, "formation": 0.20,
}

# ── Realm power ───────────────────────────────────────────────────────────────
# Additive bonus to final_dmg_bonus per total cultivation stage.
# At max (81 body + 81 qi + 81 formation = 243 stages): ~+194 % damage.
REALM_POWER_BONUS_PER_STAGE: float = 0.008  # +0.8 % per stage

# ── Enemy scaling ─────────────────────────────────────────────────────────────
# HP scale: realm_scale = 1.0 + (player_realm_total / ENEMY_SCALE_MAX) * ENEMY_HP_SCALE_FACTOR
ENEMY_SCALE_MAX: int = 81               # denominator (max single-axis stages)
ENEMY_HP_SCALE_FACTOR: float = 2.0      # at max realm: 3× base HP
ENEMY_DMG_BONUS_SCALE: float = 1.5     # at max realm: +150 % damage
ENEMY_BASE_ELEM_RES: float = 0.10      # 10% elemental resistance for enemy's own element

# Fallback base combat stats by rank — used when enemy JSON omits base_atk/matk/def/evasion.
# These are BEFORE realm_scale is applied.
ENEMY_RANK_BASE_ATK: dict[str, int] = {
    "pho_thong": 30, "cuong_gia": 60, "dai_nang": 100, "chi_ton": 180,
}
ENEMY_RANK_BASE_MATK: dict[str, int] = {
    "pho_thong": 30, "cuong_gia": 60, "dai_nang": 100, "chi_ton": 180,
}
ENEMY_RANK_BASE_DEF: dict[str, int] = {
    "pho_thong": 20, "cuong_gia": 40, "dai_nang": 60, "chi_ton": 100,
}
ENEMY_RANK_BASE_EVASION: dict[str, int] = {
    "pho_thong": 0, "cuong_gia": 50, "dai_nang": 100, "chi_ton": 200,
}

# ── Enemy realm levels (1-10) ─────────────────────────────────────────────────
# Additional combat-stat multiplier per realm level (HP unaffected — calibrated in JSON).
# Level 1 = weakest (early game / pho_thong), Level 10 = strongest (late / chi_ton).
# Applied on top of the existing player-realm-based scaling in build_enemy_combatant.
ENEMY_REALM_LEVEL_STAT_MULT: dict[int, float] = {
    1: 1.00, 2: 1.05, 3: 1.12, 4: 1.20, 5: 1.30,
    6: 1.42, 7: 1.56, 8: 1.72, 9: 1.90, 10: 2.10,
}

# ── Player defaults ───────────────────────────────────────────────────────────
BASE_MP_REGEN_PCT: float = 0.01   # 1 % MP per turn baseline (before bonuses)
MAX_FINAL_DMG_REDUCE: float = 0.75  # damage-reduction hard cap (buffs + debuffs)
