"""Character combat stat computation — single source of truth.

Every system that needs derived combat stats (combat, dungeon, tribulation,
status embed) calls compute_combat_stats() so the math can never diverge
between display and actual combat.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.game.models.character import Character
from src.game.constants.balance import (
    REALM_POWER_BONUS_PER_STAGE,
    BASE_MP_REGEN_PCT,
    COOLDOWN_REDUCE_CAP,
    MAX_FINAL_DMG_REDUCE,
    MAX_ELEMENTAL_RES,
    DEFAULT_MANA_STACK_CAP,
    DEFAULT_SHIELD_RECHARGE_DELAY,
)
from src.game.constants.elements import ALL_ELEMENTS, RESISTANCE_KEYS
from src.game.engine import linh_can_effects as lc_effects
from src.game.systems.combat.helpers import meta_per_stack_pct, meta_stack_cap

# Stack-DoT per-stack damage defaults sourced from EffectMeta entries in
# effects.py — designers tune the values there; this module just reflects
# them. Stack caps no longer flow through here — they're read at runtime via
# ``effective_stack_cap`` (meta.stack_cap + ``stack_cap_bonuses``).
_BURN_PCT_DEFAULT   = meta_per_stack_pct("burn")
_BLEED_PCT_DEFAULT  = meta_per_stack_pct("bleed")
_SHOCK_PCT_DEFAULT  = meta_per_stack_pct("shock")
_POISON_PCT_DEFAULT = meta_per_stack_pct("poison")


# ── Per-constitution config-flag registry ──────────────────────────────────
# Each entry is ``(combatstats_field, bonus_key, kind)``. These are the
# "config-only" passive flags individual constitution bodies stamp into their
# stat_bonuses (read by procs / hooks / combat_hit rather than applied as real
# stats). They share a uniform shape: read from ``bonuses`` (and additively /
# OR-merged from ``equip_stats``) under ``bonus_key``, then passed straight
# through to the matching ``CombatStats`` field — no mid-function use. The
# bonus_key may ALIAS the field name (e.g. ``kim_periodic_crit_interval`` →
# ``bleed_hunter_periodic_interval``); the alias lives here so a new body adds
# ONE dataclass field + ONE row instead of four scattered edits.
#
# ``kind`` drives both the coercion (float/int/bool) and the equip-merge rule:
# numeric kinds add, bool kinds OR — exactly reproducing the per-line behavior
# the four hand-written blocks used to encode.
_CONSTITUTION_FLAG_FIELDS: list[tuple[str, str, type]] = [
    # Thiên Cương Phá Sát Thể (Kim killing-aura)
    ("kim_pha_giap_on_hit_chance",       "kim_pha_giap_on_hit_chance",        float),
    ("kim_pha_giap_high_sat_khi_chance", "kim_pha_giap_high_sat_khi_chance",  float),
    ("kim_sword_splash_at_max_sat_khi",  "kim_sword_splash_at_max_sat_khi",   bool),
    ("kim_sword_splash_base_chance",     "kim_sword_splash_base_chance",      float),
    ("kim_sword_splash_crit_coeff",      "kim_sword_splash_crit_coeff",       float),
    ("kim_sword_splash_chance_cap",      "kim_sword_splash_chance_cap",       float),
    # Thái Bạch Canh Kim Thể (Kim crit-bleeder) — note the bonus-key aliases.
    ("bleed_hunter_crit_chance_bonus",   "kim_bleed_hunter_crit_chance_bonus", float),
    ("bleed_hunter_crit_dmg_bonus",      "kim_bleed_hunter_crit_dmg_bonus",    float),
    ("bleed_hunter_periodic_interval",   "kim_periodic_crit_interval",         int),
    # Huyền Âm Thiên Ma Thể (Ám shadow-mage) — note the bonus-key aliases.
    ("shadow_stack_on_hit",              "shadow_stack_on_hit",               bool),
    ("nhap_ma_dmg_bonus",                "nhap_ma_dmg_bonus",                 float),
    ("nhap_ma_interval",                 "am_auto_nhap_ma_interval",          int),
    ("nhap_ma_duration",                 "am_nhap_ma_duration",               int),
    # Chân Dương Bất Diệt Thể (Hỏa phoenix tank-mage)
    ("hoa_burning_amp_chance",           "hoa_burning_amp_chance",            float),
    ("hoa_burning_amp_pct",              "hoa_burning_amp_pct",               float),
    ("hoa_revive_upgraded",              "hoa_revive_upgraded",               bool),
    ("hoa_revive_charges",               "hoa_revive_charges",                int),
    ("hoa_revive_hp_pct_l9",             "hoa_revive_hp_pct_l9",              float),
    # Huyền Thủy Trường Sinh Thể (Thủy tidal counter-puncher)
    ("thuy_tide_intake_pct",             "thuy_tide_intake_pct",             float),
    ("thuy_reservoir_cap_matk_scale",    "thuy_reservoir_cap_matk_scale",    float),
    ("thuy_retaliate_freeze_chance",     "thuy_retaliate_freeze_chance",     float),
    ("thuy_shatter_tide_pct",            "thuy_shatter_tide_pct",            float),
    ("thuy_tidal_flood_enabled",         "thuy_tidal_flood_enabled",         bool),
    ("thuy_tidal_flood_interval",        "thuy_tidal_flood_interval",        int),
    ("thuy_tidal_release_pct",           "thuy_tidal_release_pct",           float),
    ("thuy_tidal_depth_per_turn",        "thuy_tidal_depth_per_turn",        float),
    ("thuy_tidal_depth_mult_cap",        "thuy_tidal_depth_mult_cap",        float),
    ("thuy_tidal_refill_pct",            "thuy_tidal_refill_pct",            float),
    # Trường Xuân Linh Mộc Thể (Mộc poison / eternal-spring tank)
    ("moc_vs_slowed_dmg_bonus",          "moc_vs_slowed_dmg_bonus",          float),
    ("moc_regen_per_enemy_debuff",       "moc_regen_per_enemy_debuff",       float),
    ("moc_regen_debuff_cap",             "moc_regen_debuff_cap",             int),
    ("moc_guaranteed_poison_on_attack",  "moc_guaranteed_poison_on_attack",  bool),
    ("moc_guaranteed_poison_stacks",     "moc_guaranteed_poison_stacks",     int),
    ("moc_undying_spring_enabled",       "moc_undying_spring_enabled",       bool),
    ("moc_undying_cooldown_turns",       "moc_undying_cooldown_turns",       int),
    ("moc_undying_min_hp",               "moc_undying_min_hp",               int),
    ("moc_undying_heal_reduce_gate",     "moc_undying_heal_reduce_gate",     float),
    # Kim Cang Bất Hoại Thể (Thổ indestructible shield body) — config flags.
    # ``dia_mach_stacks`` is a runtime counter (Combatant-only, excluded).
    ("dia_mach_per_regen",                 "dia_mach_per_regen",                 bool),
    ("tho_phys_immune_chance",             "tho_phys_immune_chance",             float),
    ("tho_phys_immune_high_shield_chance", "tho_phys_immune_high_shield_chance", float),
    ("tho_phys_immune_shield_gate",        "tho_phys_immune_shield_gate",        float),
    ("tho_auto_slow_enabled",              "tho_auto_slow_enabled",              bool),
    ("tho_earth_aura_shield_pct",          "tho_earth_aura_shield_pct",          float),
    # Phi Thiên Lăng Vân Thể (Phong dodge-counter bruiser) — config flags.
    # Runtime counters (phong_van_stacks, phong_crit_armed,
    # phong_unevadable_armed, phong_skill_cast_counter) are Combatant-only
    # and excluded here.
    ("phong_eva_phong_dmg_per_300",       "phong_eva_phong_dmg_per_300",       float),
    ("phong_van_dodge_stack",             "phong_van_dodge_stack",             bool),
    ("phong_dodge_arms_crit",             "phong_dodge_arms_crit",             bool),
    ("phong_dodge_crit_applies_an_phong", "phong_dodge_crit_applies_an_phong", bool),
    ("phong_unevadable_interval",         "phong_unevadable_interval",         int),
    ("phong_cuon_bay_on_crit_chance",     "phong_cuon_bay_on_crit_chance",     float),
    # Thiên Lôi Cường Thể (Lôi shock/speed nuker)
    ("loi_te_liet_on_crit_chance",        "loi_te_liet_on_crit_chance",        float),
    ("loi_charge_enabled",                "loi_charge_enabled",                bool),
    ("loi_spd_advantage_per_10",          "loi_spd_advantage_per_10",          float),
    ("loi_spd_advantage_cap",             "loi_spd_advantage_cap",             float),
    ("loi_reflex_bonus_attack",           "loi_reflex_bonus_attack",           bool),
    ("loi_bonus_true_dmg_pct",            "loi_bonus_true_dmg_pct",            float),
    # Tịnh Quang Hộ Pháp Thể (Quang guardian)
    ("quang_blind_stack",                 "quang_blind_stack",                 bool),
    ("quang_self_cleanse_interval",       "quang_self_cleanse_interval",       int),
    ("quang_self_cleanse_count",          "quang_self_cleanse_count",          int),
    ("quang_guardian_summon_matk_pct",    "quang_guardian_summon_matk_pct",    float),
    ("quang_judgment_strip_chance",       "quang_judgment_strip_chance",       float),
    ("quang_judgment_applies_pha_giap",   "quang_judgment_applies_pha_giap",   bool),
    # Quang silence-on-crit — the pre-existing pattern these mirror.
    ("silence_on_crit_pct",              "silence_on_crit_pct",               float),
    # Hoàng Cổ Thánh Thể (Universal Saint Body) — config flags.
    # Runtime counters (saint_crit_turn_counter / saint_realm_turn_counter /
    # saint_crit_armed) are Combatant-only and excluded here.
    ("saint_qilin_cleanse_chance",       "saint_qilin_cleanse_chance",        float),
    ("saint_periodic_crit_interval",     "saint_periodic_crit_interval",      int),
    ("saint_mp_on_hit_pct",              "saint_mp_on_hit_pct",               float),
    ("saint_realm_enabled",              "saint_realm_enabled",               bool),
    ("saint_realm_interval",             "saint_realm_interval",              int),
    ("saint_realm_duration",             "saint_realm_duration",              int),
    # Hỗn Nguyên Vô Cực Thể (Universal omni-element amplifier) — config flags.
    # ``omni_sum_element_dmg`` folds the WHOLE element_dmg_bonus dict into the
    # skill's own-element bonus (Vạn Nguyên Quy Nhất). ``omni_res_ignore_chance``
    # is the per-cast chance to zero the target's elemental resistance.
    ("omni_sum_element_dmg",             "omni_sum_element_dmg",              bool),
    ("omni_res_ignore_chance",           "omni_res_ignore_chance",           float),
    # Thiên Địa Nhân Hòa Thể (Universal Hòa Khí stack-scaler) — config flags.
    # ``harmony_stacks`` is a runtime counter (Combatant-only, excluded). The
    # L1 ramp + L3/L9 thresholds read it via scaling_rules ``stat:harmony_stacks``.
    ("harmony_stack_per_turn",           "harmony_stack_per_turn",           int),
    ("harmony_stack_cap",                "harmony_stack_cap",                int),
    ("harmony_backlash_pct_per_stack",   "harmony_backlash_pct_per_stack",   float),
    ("harmony_backlash_min_stacks",      "harmony_backlash_min_stacks",      int),
    ("harmony_l9_cleanse",               "harmony_l9_cleanse",               int),
    # Bắc Minh Băng Phách Thể (Thủy ice/freeze/MP-drain disruptor) — config flags.
    # Runtime counters (han_khi_stacks, han_khi_mp_drained_total) are
    # Combatant-only and excluded. All on-hit logic lives in run_on_hit_procs.
    ("bm_cold_aura_enabled",             "bm_cold_aura_enabled",             bool),
    ("bm_freeze_on_attack_chance",       "bm_freeze_on_attack_chance",       float),
    ("bm_mp_drain_pct",                  "bm_mp_drain_pct",                  float),
    ("bm_mp_drain_heal_pct",             "bm_mp_drain_heal_pct",             float),
    ("bm_han_khi_cap",                   "bm_han_khi_cap",                   int),
    ("bm_heal_reduce_chance",            "bm_heal_reduce_chance",            float),
    ("bm_heal_reduce_vs_frozen_chance",  "bm_heal_reduce_vs_frozen_chance",  float),
    ("bm_burst_freeze_turns",            "bm_burst_freeze_turns",            int),
    ("bm_burst_drain_pct",               "bm_burst_drain_pct",               float),
    # Huyền Minh Nhược Thể (Thủy anti-physical attrition disruptor) — config flags.
    # ``phys_dmg_reduce_pct`` is a real defender-side stat read in casting.py;
    # the rest are config flags consumed by run_huyen_minh_procs + the per-cast
    # corrosion + the on-evade Uyên hook. Runtime counters (hm_uyen_stacks,
    # hm_mp_drained_total) are Combatant-only and excluded.
    ("phys_dmg_reduce_pct",              "phys_dmg_reduce_pct",              float),
    ("hm_mp_drain_pct",                  "hm_mp_drain_pct",                  float),
    ("hm_mp_drain_heal_pct",             "hm_mp_drain_heal_pct",             float),
    ("hm_hp_siphon_pct",                 "hm_hp_siphon_pct",                 float),
    ("hm_uyen_cap",                      "hm_uyen_cap",                      int),
    ("hm_corrode_poison_stacks",         "hm_corrode_poison_stacks",         int),
    ("hm_corrode_bleed_stacks",          "hm_corrode_bleed_stacks",          int),
    ("hm_drown_burst_drain_pct",         "hm_drown_burst_drain_pct",         float),
]


def _read_constitution_flags(
    bonuses: dict, equip_stats: dict | None,
) -> dict[str, float | int | bool]:
    """Read every per-constitution config flag from ``bonuses`` (+ ``equip_stats``).

    Reproduces the old per-line reads byte-for-byte: each flag is coerced to its
    ``kind`` from ``bonuses[bonus_key]`` (default 0 / False), then, when
    ``equip_stats`` is present, merged from ``equip_stats[bonus_key]`` — numeric
    kinds add, bool kinds OR. Returns a ``{combatstats_field: value}`` map ready
    to splat into the ``CombatStats(...)`` constructor.
    """
    out: dict[str, float | int | bool] = {}
    for field_name, bonus_key, kind in _CONSTITUTION_FLAG_FIELDS:
        if kind is bool:
            value: float | int | bool = bool(bonuses.get(bonus_key, False))
            if equip_stats:
                value = value or bool(equip_stats.get(bonus_key, False))
        elif kind is int:
            value = int(bonuses.get(bonus_key, 0))
            if equip_stats:
                value += int(equip_stats.get(bonus_key, 0))
        else:  # float
            value = float(bonuses.get(bonus_key, 0.0))
            if equip_stats:
                value += float(equip_stats.get(bonus_key, 0.0))
        out[field_name] = value
    return out


def active_formation_gem_keys(player) -> list[str]:
    """Flattened gem_keys across EVERY active formation slot.

    ``player.active_formation`` holds a comma-separated list of formation keys
    (one per active slot); this returns the concatenation of each active
    formation's inlaid gems. Returns the single-slot list when the player has
    only one formation equipped.
    """
    from src.game.systems.cultivation import get_active_formations

    actives = get_active_formations(getattr(player, "active_formation", None))
    if not actives:
        return []
    by_key = {
        getattr(f, "formation_key", None): list((f.gem_slots or {}).values())
        for f in getattr(player, "formations", None) or []
    }
    out: list[str] = []
    for key in actives:
        out.extend(by_key.get(key) or [])
    return out


def active_formation_gem_map(player) -> dict[str, list[str]]:
    """Per-formation gem lists keyed by formation_key, restricted to the
    formations currently in an active slot. Used by compute_formations_bonuses
    so each formation's own threshold bonuses + per-gem elemental bonuses fire
    with the right gem set — flattening via ``active_formation_gem_keys``
    would misattribute gems across formations.
    """
    from src.game.systems.cultivation import get_active_formations

    actives = set(get_active_formations(getattr(player, "active_formation", None)))
    if not actives:
        return {}
    return {
        getattr(f, "formation_key"): list((f.gem_slots or {}).values())
        for f in getattr(player, "formations", None) or []
        if getattr(f, "formation_key", None) in actives
    }


@dataclass
class CombatStats:
    """All derived stats for one character — cultivation + formation + constitution + linh_can + equipment.

    ``mp_max`` is the *effective* cap after formation reservation; ``mp_reserved``
    exposes how much was locked so UIs can show both numbers.
    """
    hp_max: int
    mp_max: int          # usable MP pool (total - reserved)
    atk: int
    matk: int
    def_stat: int
    spd: int
    crit_rating: int
    crit_dmg_rating: int
    evasion_rating: int
    crit_res_rating: int
    accuracy_rating: int
    final_dmg_bonus: float
    final_dmg_reduce: float
    hp_regen_pct: float
    hp_regen_flat: int
    mp_regen_pct: float
    mp_regen_flat: int
    heal_pct: float
    cooldown_reduce: float
    burn_on_hit_pct: float
    slow_on_hit_pct: float
    paralysis_on_crit: bool
    freeze_on_skill_chance: float
    poison_immunity: bool
    debuff_immune_pct: float
    # ── Fire-DoT build ────────────────────────────────────────────────────
    # Stack cap routed through ``Combatant.stack_cap_bonuses`` (gear /
    # constitutions / linh_can write ``dot_stack_cap_bonus: {burn: N}``);
    # ``effective_stack_cap`` folds the resolved value on top of
    # ``EffectMeta.stack_cap``.
    burn_per_stack_pct: float = _BURN_PCT_DEFAULT
    bonus_dmg_vs_burn: float = 0.0
    dot_can_crit: bool = False
    # ── Kim (bleed) build ─────────────────────────────────────────────────
    bleed_per_stack_pct: float = _BLEED_PCT_DEFAULT
    bleed_on_hit_pct: float = 0.0
    # Mộc (Trường Xuân Linh Mộc) on-hit poison chance — rides the generic
    # on-hit proc table like burn/bleed. 0.0 → never procs.
    poison_on_hit_pct: float = 0.0
    bleed_heal_reduce: float = 0.0
    true_dmg_pct: float = 0.0
    # Generic life-steal — heal actor for X% of damage dealt on each hit.
    # Distinct from ``dot_leech_pct`` (DoT-only) and ``soul_drain_on_hit_pct``
    # (chance-gated, drains target hp_max). Routed through ``_apply_heal``
    # so bleed-heal-reduction and heal_can_crit still apply.
    life_steal_pct: float = 0.0
    # Hybrid scaling: ``crit_dmg_rating_to_dmg_pct × crit_dmg_rating`` is
    # added as flat damage to the base roll (engine/damage/base.py). Lets
    # crit-damage stacking double-dip: rating still controls crit-spike
    # multiplier AND adds reliable per-hit floor damage. Crucial for the
    # Phá Thiên-style burst constitutions where crit stat is the primary
    # offensive investment.
    crit_dmg_rating_to_dmg_pct: float = 0.0
    # ── Moc (wood/poison-leech/heal) build ────────────────────────────────
    dot_leech_pct: float = 0.0
    damage_from_heal_pct: float = 0.0
    damage_bonus_from_hp_pct: float = 0.0
    # ── Thủy (water/mana/mirror) build ────────────────────────────────────
    reflect_pct: float = 0.0
    reflect_applies_effects: bool = False
    damage_bonus_from_mp_pct: float = 0.0
    mp_leech_pct: float = 0.0
    mana_stack_cap: int = DEFAULT_MANA_STACK_CAP
    mana_stack_per_attack: int = 0
    mana_stack_dmg_bonus: float = 0.0
    # ── Thổ (earth/shield/thorn) build ────────────────────────────────────
    shield_regen_pct: float = 0.0
    shield_regen_flat: int = 0
    shield_max_base: int = 0
    shield_max_flat: int = 0
    shield_max_pct: float = 0.0
    # Fraction of hp_max converted into shield_max_base (cap pool grows by the
    # same amount HP shrinks). Hard-capped at 0.95 so HP can't drop to 0.
    hp_to_shield_pct: float = 0.0
    # Per-attack MATK / ATK bonus = current_shield × this fraction. Scales
    # magic / physical damage with the holder's current Energy Shield pool so
    # depleted shield = depleted punch. Symmetric pair so mage and physical
    # builds can both pivot a shield-tank into offense.
    matk_from_shield_pct: float = 0.0
    atk_from_shield_pct: float = 0.0
    # ── Endure (Đế Sinh Mộc Thể "Cội Nguồn Bất Tận") ──────────────────────
    # When a hit would kill the holder, HP is set to ``hp_max × endure_threshold_pct``
    # instead and an internal cooldown is engaged. Different from BuffBatTu
    # (single-use) and phoenix_revive (revive-from-death + buff): endure is a
    # repeatable damage-floor with a turn-based cooldown.
    endure_threshold_pct: float = 0.0
    endure_cooldown: int = 0
    # ── Cleanse Heal (Đế Bạch Liên Thể "Liên Hoa Tịnh Hóa") ──────────────
    # Each successful cleanse pulse restores ``hp_max × cleanse_heal_pct`` HP
    # on top of the existing MP-restore + barrier mechanics. Stacks with
    # cleanse_on_turn_pct so a high-cleanse build heals every turn.
    cleanse_heal_pct: float = 0.0
    # ── Cleanse Retaliate (Đế Tịnh Quang Thể "Tịnh Hóa Phản Đòn") ───────
    # Each successful cleanse fires retaliate damage = ``matk × this_pct`` at
    # the cleanser's opponent. Treated as Quang-element on the wire.
    cleanse_retaliate_dmg_pct: float = 0.0
    # ── Kill Streak (Đế Sát Kim Thể "Sát Khí Đại Thành") ───────────────
    # Each enemy killed grants a permanent (this combat) +final_dmg_bonus
    # equal to ``kill_buff_per_kill_pct``, capped at ``kill_buff_cap`` stacks.
    # Resets between dungeon entries (the player_c is rebuilt each dungeon).
    kill_buff_per_kill_pct: float = 0.0
    kill_buff_cap: int = 0
    # ── Multi-Strike (Phong/Lôi/Kim — "Liên Phong / Vạn Kiếm / Lôi Diên Đả") ─
    # Per-attack roll: with ``multi_strike_pct`` chance, the attack lands an
    # extra hit at ``multi_strike_dmg_pct`` of the original damage. Default
    # 50% damage on the second strike. Fits speed/lightning/wind/sword themes.
    multi_strike_pct: float = 0.0
    multi_strike_dmg_pct: float = 0.50
    # Extra echoes added to ``per_hit_followup`` formation skills (e.g. the
    # 10-gem Thập Nhị Đô Thiên Thần Sát threshold). Read in
    # ``fire_formation_skills``; integer count.
    formation_echo_bonus: int = 0
    # Multiplier on a ``per_hit_followup`` formation skill's ``base_dmg``.
    # 0.55 = +55%. Sourced from gem thresholds on the matching formation;
    # applied in ``cast_skill`` before damage roll. Skill-agnostic on its
    # own — gated on ``per_hit_followup: true`` so it only amps passive
    # echo formations and never leaks to standard skill casts.
    formation_skill_dmg_bonus: float = 0.0
    # Bumps the effective ``limit`` of summon_spec'd skills by N — used by
    # Thiên Giới Thẩm Phán's tier-10 gem threshold to allow 2 Đại Thiên Sứ
    # at once instead of the spec's default 1. Shared field, so any future
    # singleton-summon skill that wants to elevate its cap can read it.
    summon_limit_bonus: int = 0
    shield_recharge_delay: int = DEFAULT_SHIELD_RECHARGE_DELAY
    damage_bonus_from_shield_pct: float = 0.0
    thorn_pct: float = 0.0
    thorn_from_shield: bool = False
    stun_on_hit_pct: float = 0.0
    # ── Per-constitution config flags (registry-driven) ───────────────────
    # These fields are populated via ``_read_constitution_flags`` /
    # ``_CONSTITUTION_FLAG_FIELDS`` — a new body adds ONE field here plus ONE
    # registry row (alias + kind), not four scattered edits. Read by
    # procs / hooks / combat_hit, not applied as raw stats.
    #   Thiên Cương Phá Sát Thể (Kim killing-aura)
    kim_pha_giap_on_hit_chance: float = 0.0
    kim_pha_giap_high_sat_khi_chance: float = 0.0
    kim_sword_splash_at_max_sat_khi: bool = False
    kim_sword_splash_base_chance: float = 0.0
    kim_sword_splash_crit_coeff: float = 0.0
    kim_sword_splash_chance_cap: float = 0.0
    #   Thái Bạch Canh Kim Thể (Kim crit-bleeder)
    bleed_hunter_crit_chance_bonus: float = 0.0
    bleed_hunter_crit_dmg_bonus: float = 0.0
    bleed_hunter_periodic_interval: int = 0
    #   Huyền Âm Thiên Ma Thể (Ám shadow-mage)
    shadow_stack_on_hit: bool = False
    nhap_ma_dmg_bonus: float = 0.0
    nhap_ma_interval: int = 0
    nhap_ma_duration: int = 0
    #   Chân Dương Bất Diệt Thể (Hỏa phoenix tank-mage)
    hoa_burning_amp_chance: float = 0.0
    hoa_burning_amp_pct: float = 0.0
    hoa_revive_upgraded: bool = False
    hoa_revive_charges: int = 0
    hoa_revive_hp_pct_l9: float = 0.0
    #   Huyền Thủy Trường Sinh Thể (Thủy tidal counter-puncher)
    thuy_tide_intake_pct: float = 0.0
    thuy_reservoir_cap_matk_scale: float = 0.0
    thuy_retaliate_freeze_chance: float = 0.0
    thuy_shatter_tide_pct: float = 0.0
    thuy_tidal_flood_enabled: bool = False
    thuy_tidal_flood_interval: int = 0
    thuy_tidal_release_pct: float = 0.0
    thuy_tidal_depth_per_turn: float = 0.0
    thuy_tidal_depth_mult_cap: float = 0.0
    thuy_tidal_refill_pct: float = 0.0
    #   Trường Xuân Linh Mộc Thể (Mộc poison / eternal-spring tank)
    moc_vs_slowed_dmg_bonus: float = 0.0
    moc_regen_per_enemy_debuff: float = 0.0
    moc_regen_debuff_cap: int = 0
    moc_guaranteed_poison_on_attack: bool = False
    moc_guaranteed_poison_stacks: int = 0
    moc_undying_spring_enabled: bool = False
    moc_undying_cooldown_turns: int = 0
    moc_undying_min_hp: int = 0
    moc_undying_heal_reduce_gate: float = 0.0
    #   Kim Cang Bất Hoại Thể (Thổ indestructible shield body)
    dia_mach_per_regen: bool = False
    tho_phys_immune_chance: float = 0.0
    tho_phys_immune_high_shield_chance: float = 0.0
    tho_phys_immune_shield_gate: float = 0.0
    tho_auto_slow_enabled: bool = False
    tho_earth_aura_shield_pct: float = 0.0
    #   Phi Thiên Lăng Vân Thể (Phong dodge-counter bruiser)
    phong_eva_phong_dmg_per_300: float = 0.0
    phong_van_dodge_stack: bool = False
    phong_dodge_arms_crit: bool = False
    phong_dodge_crit_applies_an_phong: bool = False
    phong_unevadable_interval: int = 0
    phong_cuon_bay_on_crit_chance: float = 0.0
    #   Thiên Lôi Cường Thể (Lôi shock/speed nuker)
    loi_te_liet_on_crit_chance: float = 0.0
    loi_charge_enabled: bool = False
    loi_spd_advantage_per_10: float = 0.0
    loi_spd_advantage_cap: float = 0.0
    loi_reflex_bonus_attack: bool = False
    loi_bonus_true_dmg_pct: float = 0.0
    #   Tịnh Quang Hộ Pháp Thể (Quang guardian)
    quang_blind_stack: bool = False
    quang_self_cleanse_interval: int = 0
    quang_self_cleanse_count: int = 0
    quang_guardian_summon_matk_pct: float = 0.0
    quang_judgment_strip_chance: float = 0.0
    quang_judgment_applies_pha_giap: bool = False
    #   Hoàng Cổ Thánh Thể (Universal Saint Body)
    saint_qilin_cleanse_chance: float = 0.0
    saint_periodic_crit_interval: int = 0
    saint_mp_on_hit_pct: float = 0.0
    saint_realm_enabled: bool = False
    saint_realm_interval: int = 0
    saint_realm_duration: int = 0
    #   Hỗn Nguyên Vô Cực Thể (Universal omni-element amplifier)
    omni_sum_element_dmg: bool = False
    omni_res_ignore_chance: float = 0.0
    #   Thiên Địa Nhân Hòa Thể (Universal Hòa Khí stack-scaler)
    harmony_stack_per_turn: int = 0
    harmony_stack_cap: int = 0
    harmony_backlash_pct_per_stack: float = 0.0
    harmony_backlash_min_stacks: int = 0
    harmony_l9_cleanse: int = 0
    #   Bắc Minh Băng Phách Thể (Thủy ice/freeze/MP-drain disruptor)
    bm_cold_aura_enabled: bool = False
    bm_freeze_on_attack_chance: float = 0.0
    bm_mp_drain_pct: float = 0.0
    bm_mp_drain_heal_pct: float = 0.0
    bm_han_khi_cap: int = 0
    bm_heal_reduce_chance: float = 0.0
    bm_heal_reduce_vs_frozen_chance: float = 0.0
    bm_burst_freeze_turns: int = 0
    bm_burst_drain_pct: float = 0.0
    # Huyền Minh Nhược Thể (Thủy anti-physical attrition disruptor)
    phys_dmg_reduce_pct: float = 0.0
    hm_mp_drain_pct: float = 0.0
    hm_mp_drain_heal_pct: float = 0.0
    hm_hp_siphon_pct: float = 0.0
    hm_uyen_cap: int = 0
    hm_corrode_poison_stacks: int = 0
    hm_corrode_bleed_stacks: int = 0
    hm_drown_burst_drain_pct: float = 0.0
    # ── Lôi (lightning/shock/speed) build ─────────────────────────────────
    # Stack cap routed through ``stack_cap_bonuses`` (gear adds
    # ``shock_stack_cap_bonus``).
    shock_per_stack_pct: float = _SHOCK_PCT_DEFAULT
    shock_on_hit_pct: float = 0.0
    turn_steal_pct: float = 0.0
    # ── Poison stacks (Mộc / Âm) ──────────────────────────────────────────
    # Stack cap routed through ``stack_cap_bonuses`` (gear / constitutions /
    # linh_can write ``dot_stack_cap_bonus: {poison: N}``).
    poison_per_stack_pct: float = _POISON_PCT_DEFAULT
    # ── Phong (wind/evasion/mark) build ───────────────────────────────────
    mark_on_hit_pct: float = 0.0
    damage_bonus_from_evasion_pct: float = 0.0
    # ── Quang (light/silence/anti-heal) build ─────────────────────────────
    # ``silence_on_crit_pct`` is also registry-driven (see
    # ``_CONSTITUTION_FLAG_FIELDS``) — it stays here with its Quang kin for
    # locality, but its read / equip-merge / passthrough run via the flag helper.
    silence_on_crit_pct: float = 0.0
    heal_reduce_on_hit_pct: float = 0.0
    # Tịnh Quang (guardian) on-hit blind chance — rides the generic _ON_HIT_PROCS
    # table (DEBUFF_LOA_MAT). Was previously a Combatant-only field with no data
    # path; wired here so constitutions/equipment can grant it.
    blind_on_hit_pct: float = 0.0
    cleanse_on_turn_pct: float = 0.0
    barrier_on_cleanse: bool = False
    # ── Âm (shadow/soul-devour) build ─────────────────────────────────────
    soul_drain_on_hit_pct: float = 0.0
    stat_steal_on_hit_pct: float = 0.0
    # Conditional crit amps vs targets in specific debuff states — single
    # nested dict (mirrors element_pen). Outer key = state name ("bleed",
    # "marked", "drained"); inner keys = "rating" / "dmg". JSON keeps flat
    # ``crit_rating_vs_<state>`` / ``crit_dmg_vs_<state>`` keys for backwards
    # compat with equipment/constitution data; translated into this dict in
    # build_combat_stats. Read at hit-time in damage/combat_hit.py.
    crit_amp_vs: dict[str, dict[str, int]] = field(default_factory=dict)
    # ── Cross-element penetration — single dict source of truth ───────────
    # Passive attacker-side stat. Stacks additively with target debuffs
    # (DebuffXuyenThau<Elem>, DebuffXeRach via res_all).
    element_pen: dict[str, float] = field(default_factory=dict)
    # ── Mộc + Quang shared: heals may crit (×1.5) ─────────────────────────
    heal_can_crit: bool = False
    # ── DoT damage amplifiers (cross-build) ───────────────────────────────
    dot_dmg_bonus: float = 0.0
    # Per-DoT-kind damage amps (additive on top of the cross-kind
    # ``dot_dmg_bonus``). Keyed by stack_kind (``burn`` / ``bleed`` /
    # ``poison``). JSON / equipment / linh_can keep flat keys
    # ``<kind>_dmg_bonus`` — translated into this dict in build_combat_stats.
    dot_dmg_bonus_by_kind: dict[str, float] = field(default_factory=dict)
    # Rare late-game flag: switch DoTs from the standard atk/matk-power model
    # to a %HP-of-target model on this combatant's outgoing DoTs.
    dot_scales_hp_pct: bool = False
    # Per-turn fire aura — deals hp_max × pct to opponent each turn.
    solar_aura_pct: float = 0.0
    # Per-turn moc-leech aura — damages opponent + heals holder for same amount.
    wither_aura_pct: float = 0.0
    # Niết Bàn Trùng Sinh — once-per-combat revive (HP-restore fraction +
    # post-revive stat buff fraction). 0.0 = no revive.
    phoenix_revive_pct: float = 0.0
    phoenix_revive_buff_pct: float = 0.0
    # Thôn Thiên Ma Khí — once-per-fight stat drain at combat start.
    stat_drain_aura_pct: float = 0.0
    # Thánh Tuyền Thể — fraction of incoming damage spread across N turns.
    damage_defer_turns: int = 0
    damage_defer_pct: float = 0.0
    # Hào Quang Củng Cố (Fortify Aura) — endgame Hoang Cổ Thánh Thể payoff.
    # Each turn adds one ``fortify_stack`` (capped at ``fortify_stack_cap``);
    # each stack contributes ``fortify_per_turn_pct`` to BOTH final_dmg_bonus
    # and final_dmg_reduce. After taking non-DoT damage, the holder gains an
    # extra ``fortify_post_hit_dr_pct`` damage reduction for 1 follow-up turn.
    # Combat code: stacks tick in CombatSession._process_periodic; brace flag
    # is set inside ``Combatant.take_damage(is_dot=False)``; both bonuses are
    # folded into ``effective_damage_reduction`` / ``build_attack_stats``.
    fortify_per_turn_pct: float = 0.0
    fortify_stack_cap: int = 0
    fortify_post_hit_dr_pct: float = 0.0
    # Hộ Thể Kiếm Cương passive — per-Kiếm-Tâm-stack damage reduction. Folds
    # into ``effective_damage_reduction`` alongside fortify. Default 0 so
    # players without the passive see no DR from sword_heart_stacks.
    sword_heart_per_stack_dr: float = 0.0
    # Kiếm Tâm Thông Minh passive — multiplicative damage amp on "Vạn Kiếm"
    # summons (both per-turn swing and the Quy Tông consume burst).
    sword_summon_dmg_amp: float = 0.0
    # Loot economy passives (constitutions / equipment) applied inside
    # CombatSession._roll_loot — additive on top of the session's baseline
    # loot_qty_multiplier (elite roll, dungeon grade) and loot_luck_pct.
    loot_qty_bonus: float = 0.0
    loot_luck_bonus: float = 0.0
    # Hậu Thổ Phong Ma Trận — tank-conversion lane percents accumulated from
    # the formation's gem ladder. Read alongside aggregated effect stats by
    # ``_hau_tho_tank_conversion_rider`` so the formation's gem progression
    # directly boosts the bonus base_dmg per attack. Permanent (formation
    # bonuses fold into here at session start) — distinct from the active-
    # effect ``BuffHauThoPhongMa.stat_bonus`` lanes which the rider also reads.
    bonus_base_dmg_per_self_hp_pct: float = 0.0
    bonus_base_dmg_per_self_shield_pct: float = 0.0
    bonus_base_dmg_per_self_def_pct: float = 0.0
    # Thổ Nguyên Hộ Pháp Trận — armor-extension lane. Permanent contribution
    # from the formation's gem ladder, layered on top of the buff's
    # ``def_applies_to_elemental_pct`` stat_bonus. Read by ``build_defense_stats``
    # and applied in the damage pipeline (``apply_armor_to_elemental``).
    def_applies_to_elemental_pct: float = 0.0
    # Generic per-element bonus dicts (constitutions / equipment).
    damage_taken_convert_pct: dict[str, float] = field(default_factory=dict)
    element_dmg_bonus: dict[str, float] = field(default_factory=dict)
    # Per-element max-res cap lifter — see Combatant.element_max_resist_bonus
    # and effects.effective_res_cap for the consumer side.
    element_max_resist_bonus: dict[str, float] = field(default_factory=dict)
    # Per-element MP cost multiplier (extra cost on top of base 1.0×) — see
    # combatant.element_mp_cost_mult for the read side.
    element_mp_cost_mult: dict[str, float] = field(default_factory=dict)
    # Cửu Khúc Hoàng Hà formation tunables — single dict (mirrors element_pen
    # / dmg_taken / toa_hon_amp). JSON declares
    # ``cuu_khuc: {per_hit, atk_reduce_pct, res_shred_pct, followup_pct,
    # followup_skill_key}``. Numbers merge additively across gem thresholds
    # (per ``_merge_bonus_dict``); ``followup_skill_key`` is last-write-wins.
    # ``per_hit`` > 0 enables the per-skill auto-stamp; the three pcts carry
    # the threshold-tier magnitudes that get snapshotted onto the target on
    # apply; ``followup_skill_key`` is the chain target at full Cửu Khúc.
    cuu_khuc: dict = field(default_factory=dict)
    # Vạn Kiếp Lôi Ngục Trận tunables — single dict (mirrors ``cuu_khuc``).
    # Sub-keys (all optional, all numeric so ``_merge_bonus_dict`` sums across
    # base + gem tiers): ``per_cast_bonus`` (extra Lôi Kiếp Ấn stacks per
    # formation tick on top of the always-1 base), ``bolt_base_dmg`` /
    # ``bolt_matk_scale`` / ``bolt_te_liet_chance`` (per-milestone Lôi Kiếp
    # Phán bolt tuning), ``capstone_base_floor`` / ``capstone_base_per_stack``
    # / ``capstone_matk_floor`` / ``capstone_matk_per_stack`` /
    # ``capstone_te_liet_chance`` (10-stack Vạn Kiếp Phán capstone tuning),
    # ``capstone_refund_mp_pct`` (Tuyệt Đỉnh-tier MP refund on capstone).
    loi_kiep_an: dict = field(default_factory=dict)
    # Thiên Lôi Tru Tà Trận tunables — single dict. Sub-keys (all optional,
    # all numeric so ``_merge_bonus_dict`` sums across base + gem tiers):
    # ``per_debuff_amp`` (dmg_taken_bonus_loi added to DebuffThienLoiAn per
    # distinct debuff on target each tick), ``debuff_count_cap`` (clamps the
    # scaling at this many debuffs), ``capstone_threshold`` (debuff count
    # that promotes Phán → Đại Phán), ``dai_phan_cd`` (turns gating Đại
    # Phán reuse), ``dai_phan_buff_strip`` (buffs ripped per capstone),
    # ``low_hp_threshold_pct`` + ``low_hp_amp_pct`` (extra dmg when target's
    # HP% is below the threshold). ``bolt_*`` / ``capstone_*`` base + scale
    # numbers tune the strike payloads on the same dict.
    thien_loi_tru_ta: dict = field(default_factory=dict)
    # Thái Cực Âm Dương Lôi Đại Trận tunables — single dict (mirrors the
    # other formation dicts). Carries phase payload tunables (Dương + Âm),
    # Thái Cực fusion damage / CD / HP-pct scaling, and the 10-gem
    # ``dual_phase_fire`` flag that collapses the rotation so both poles
    # emit every tick. Gem-tier deltas merge additively via _merge_bonus_dict
    # (and the bool dual_phase_fire is last-write-wins).
    thai_cuc_am_duong_loi: dict = field(default_factory=dict)
    # Cửu Long Thần Hỏa Trận tunables — single dict (mirrors ``cuu_khuc``).
    # Sub-keys (all optional): ``dmg_pct_bonus`` (extra dragon-tick damage),
    # ``crit_rating`` / ``crit_dmg_rating`` (per-tick crit on dragons),
    # ``stun_chance`` (per-hit CCStun roll), ``finisher_pct`` (% of damage
    # dealt during the batch fired as a burst when the last dragon expires).
    cuu_long: dict = field(default_factory=dict)
    # Xích Luyện Tỏa Hồn Trận tunables — extra stat_bonus deltas layered on
    # top of DebuffXichLuyenToaHon's base values at apply time. Mirrors
    # ``element_pen`` / ``dmg_taken``: JSON declares
    # ``toa_hon_amp: {"evasion_rating_pct": -0.05, "mp_regen_pct": -0.05, ...}``
    # and the apply-time merge in casting.py adds each entry directly to the
    # debuff's stat_bonus. Keys MUST match the debuff's stat keys verbatim
    # (no translation step).
    toa_hon_amp: dict[str, float] = field(default_factory=dict)
    # Reusable per-element "damage taken" amps the holder contributes to its
    # debuffs. Mirrors ``element_pen`` — JSON declares the bonus as
    # ``dmg_taken: {"hoa": 0.15}`` and any source (formation, equipment,
    # constitution) can write to any element. At apply time the relevant
    # entry is stamped onto a target debuff's stat_bonus under the flat
    # ``<name>_dmg_taken`` key (see ELEMENT_DMG_TAKEN_KEYS), then consumed by
    # the damage pipeline's apply_elemental step.
    dmg_taken: dict[str, float] = field(default_factory=dict)
    mp_reserved: int = 0          # MP locked by active formation
    mp_reserve_pct: float = 0.0   # fraction of raw mp_max that's reserved
    resistances: dict[str, float] = field(default_factory=dict)
    # Generic stack-cap bonuses keyed by effect_key — gear/constitution/Linh
    # Căn ``<kind>_stack_cap_bonus`` keys are routed here at build time.
    # ``effective_stack_cap`` reads from ``combatant.stack_cap_bonuses``.
    stack_cap_bonuses: dict[str, int] = field(default_factory=dict)

    @property
    def shield_max(self) -> int:
        """Persistent shield cap from base + flat + pct contributions.

        Mirrors ``Combatant.shield_cap`` but excludes runtime-only sources
        (active buffs, ``shield_max_per_spd``) — used to seed the player's
        ``shield_current`` column post-battle and at character creation.
        """
        flat = self.shield_max_base + self.shield_max_flat
        return max(0, int(flat * (1.0 + self.shield_max_pct)))


# ── Grouped-key denesting ────────────────────────────────────────────────
# JSON authors prefer the nested ``element_pen`` / ``dmg_taken`` shape
# (single key with a sub-dict per element/kind/state). The legacy bonus reads
# in build_combat_stats use flat keys (``burn_dmg_bonus``, ``crit_rating_vs_<state>``,
# …). This pass flattens grouped → flat in-place so authors get the clean
# nested JSON without forcing every read site to switch. Existing flat keys
# in older data continue to work — the denester only *adds* flat keys when a
# nested counterpart is present.

# Nested-key → ``(suffix_template, value_converter)`` describing how each
# inner key maps to a flat key. ``{}`` placeholder is replaced by the inner
# key. ``crit_amp_vs`` is special-cased below because its inner value is a
# dict, not a scalar.
_GROUPED_FLAT_KEYS: dict[str, tuple[str, type]] = {
    "dot_dmg_bonus_by_kind":   ("{}_dmg_bonus",            float),
    "dot_stack_cap_bonus":     ("{}_stack_cap_bonus",      int),
    "dot_per_stack_pct_bonus": ("{}_per_stack_pct_bonus",  float),
}


def _denest_grouped_bonuses(bonuses: dict) -> None:
    """Flatten author-friendly nested keys into the flat read-site keys.

    Mutates ``bonuses`` in place. Safe to call multiple times — pops the
    nested entry after fanning it out so a second call is a no-op.
    """
    for grouped_key, (template, conv) in _GROUPED_FLAT_KEYS.items():
        nested = bonuses.pop(grouped_key, None)
        if not nested:
            continue
        for inner_key, val in nested.items():
            flat_key = template.format(inner_key)
            bonuses[flat_key] = conv(bonuses.get(flat_key, conv(0)) + conv(val))
    crit_amp = bonuses.pop("crit_amp_vs", None)
    if crit_amp:
        for state, amps in crit_amp.items():
            if not isinstance(amps, dict):
                continue
            r = int(amps.get("rating", 0))
            d = int(amps.get("dmg", 0))
            if r:
                bonuses[f"crit_rating_vs_{state}"] = int(bonuses.get(f"crit_rating_vs_{state}", 0)) + r
            if d:
                bonuses[f"crit_dmg_vs_{state}"] = int(bonuses.get(f"crit_dmg_vs_{state}", 0)) + d


# ── Finalization helpers ─────────────────────────────────────────────────
# These run at the tail of compute_combat_stats, after all bonus sources
# (formation, constitution, linh_can, equipment) have been folded in. Each
# mutates a small slice of the final stat block; keeping them as named
# helpers makes the order of operations easy to reason about.

def _apply_formation_mp_reserve(
    mp_max: int,
    form_bonuses: dict,
    active_formation_keys: list[str] | None,
    formation_stages: int,
) -> tuple[int, int, float]:
    """Lock part of mp_max behind active formations. Two reservation
    sources stack here, both bound by the same MAX cap:
      1. Per-formation gem reservation (from active formation slots' gems)
      2. Per-formation channeling cost (the ``formation_skill_key`` tied to
         each active formation contributes its ``reserved_mp_pct``)
    Trận Đạo path reduction is already applied inside each helper, so we
    just sum and re-cap.

    Returns: (remaining mp_max, mp_reserved, reserve_pct).
    """
    from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
    from src.game.systems.cultivation import compute_formation_skill_reserve_pct

    skill_reserve_pct = compute_formation_skill_reserve_pct(
        active_formation_keys, formation_stages=formation_stages,
    )
    raw_reserve = float(form_bonuses.get("_mp_reserve_pct", 0.0)) + skill_reserve_pct
    reserve_pct = max(0.0, min(FORMATION_MAX_RESERVE_PCT, raw_reserve))
    mp_reserved = int(mp_max * reserve_pct)
    return max(0, mp_max - mp_reserved), mp_reserved, reserve_pct


def _apply_hp_to_shield(
    hp_max: int,
    shield_max_base: int,
    hp_to_shield_pct: float,
) -> tuple[int, int, float]:
    """Convert a fraction of hp_max into flat shield_max_base. Clamped at
    0.95 so HP never drops to 0. Applied last so every other hp/shield
    bonus has already been folded in.

    Returns: (hp_max, shield_max_base, clamped hp_to_shield_pct).
    """
    hp_to_shield_pct = max(0.0, min(0.95, hp_to_shield_pct))
    if hp_to_shield_pct > 0:
        converted = int(hp_max * hp_to_shield_pct)
        shield_max_base += converted
        hp_max = max(1, hp_max - converted)
    return hp_max, shield_max_base, hp_to_shield_pct


def _apply_pill_buffs(
    char: Character,
    spd_final: int,
    def_stat: int,
    atk: int,
    element_dmg_bonus: dict[str, float],
) -> tuple[int, int, int]:
    """Apply permanent combat-buff pill bonuses. Each consume of a buff_*
    /buff_element_* pill raises the matching counter (capped at
    PILL_BUFF_CAP per key); we read those counters and add
    ``count × per_pill_value`` to the relevant stat. Applied before
    toxicity so the dan_doc penalty still stacks on top.

    ``element_dmg_bonus`` is mutated in place. Returns updated scalars
    (spd_final, def_stat, atk).
    """
    pill_counts = getattr(char, "pill_buff_counts", None) or {}
    if not pill_counts:
        return spd_final, def_stat, atk

    from src.game.systems.pill_buffs import total_buff_for_stat

    spd_final += int(round(total_buff_for_stat(pill_counts, "spd")))
    def_stat += int(round(total_buff_for_stat(pill_counts, "def_stat")))
    atk += int(round(total_buff_for_stat(pill_counts, "atk")))
    for elem in ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am"):
        bump = total_buff_for_stat(pill_counts, f"element_dmg_bonus_{elem}")
        if bump:
            element_dmg_bonus[elem] = element_dmg_bonus.get(elem, 0.0) + bump
    return spd_final, def_stat, atk


def _apply_toxicity_penalty(
    char: Character,
    final_dmg_bonus: float,
    hp_regen_pct: float,
) -> tuple[float, float]:
    """Đan Độc penalty — pill toxicity drags down outgoing damage and
    slows HP regen. Applied AFTER all bonuses so it's a true override that
    equipment / constitutions can't escape. final_dmg_bonus can go
    negative; the damage pipeline multiplies by ``(1 + final_dmg_bonus)``
    which is fine for values down to -1.0 (toxicity caps at -0.25).
    """
    dan_doc = int(getattr(char, "dan_doc", 0) or 0)
    if dan_doc <= 0:
        return final_dmg_bonus, hp_regen_pct

    from src.game.systems.toxicity import (
        final_dmg_penalty as _tox_final_dmg_penalty,
        hp_regen_multiplier as _tox_hp_regen_mult,
    )
    final_dmg_bonus -= _tox_final_dmg_penalty(dan_doc)
    hp_regen_pct *= _tox_hp_regen_mult(dan_doc)
    return final_dmg_bonus, hp_regen_pct


def compute_combat_stats(
    char: Character,
    gem_count: int = 0,
    equip_stats: dict | None = None,
    gem_keys: list[str] | None = None,
    gem_keys_by_formation: dict[str, list[str]] | None = None,
) -> CombatStats:
    """Compute all derived combat stats for a player character.

    Applies (in order): formation bonuses → constitution bonuses →
    linh_can bonuses → realm_power_bonus → equipment bonuses →
    formation MP reservation (locks part of mp_max).

    Args:
        char:        Character dataclass (from DB or model layer).
        gem_count:   Number of gems inlaid in the active formation (used if
                     ``gem_keys`` is not supplied and only one slot active).
        equip_stats: Pre-computed equipment stat totals from
                     ``compute_equipment_stats(equipped_instances)``.
                     Pass None (or {}) when equipment should be ignored.
        gem_keys:    Flat gem list — valid ONLY when the character has a
                     single formation active. Multi-slot Trận Tu callers must
                     pass ``gem_keys_by_formation`` instead so threshold +
                     per-gem bonuses attach to the right formation.
        gem_keys_by_formation: ``{formation_key: [gem_keys]}`` when multiple
                     formations are active. Takes precedence over ``gem_keys``.

    Returns:
        CombatStats with every field ready for use in Combatant construction
        or status-embed display. ``mp_max`` is already reduced by reservation;
        ``mp_reserved`` carries the locked amount for UI.
    """
    from src.game.systems.cultivation import (
        compute_hp_max, compute_mp_max,
        compute_atk, compute_matk, compute_def_stat,
        compute_formations_bonuses, compute_constitution_bonuses, merge_bonuses,
        get_active_formations,
    )
    from src.game.constants.linh_can import compute_linh_can_bonuses

    # ── Bonus merge ──────────────────────────────────────────────────────────
    # Trận Đạo cultivation progress scales formation bonuses: late-game
    # formation cultivators get meaningfully stronger formation effects.
    formation_stages = char.formation_realm * 9 + char.formation_level
    active_axis = getattr(char, "active_axis", "qi") or "qi"
    active_formations = get_active_formations(char.active_formation)

    # Build a per-formation gem map. Callers that only have a flat gem list
    # (single-formation contexts) still work because a single active
    # formation always owns all the gems.
    if gem_keys_by_formation is None:
        if len(active_formations) == 1 and gem_keys:
            gem_keys_by_formation = {active_formations[0]: list(gem_keys)}
        else:
            gem_keys_by_formation = {}

    # Pass active_axis + relevant realms through so the bonus pipeline can
    # clamp past-cap entries to whatever the current path supports — a
    # player who switches off body/formation focus stops cashing in on
    # extra slots until they switch back.
    form_bonuses = compute_formations_bonuses(
        active_formations,
        active_axis,
        char.formation_realm,
        gem_keys_by_formation=gem_keys_by_formation,
        formation_stages=formation_stages,
    )
    # Constitution Process read path — flag-gated. When OFF (default) we pass
    # ``process_levels=None`` so ``compute_constitution_bonuses`` reads the flat
    # ``stat_bonuses`` exactly as before (byte-identical, never touches the
    # process code). When ON, a populated ``char.constitution_levels`` resolves
    # each equipped body at its stored level; an empty map (NPC-style Characters
    # built outside the DB pipeline) still collapses to ``None`` → inert.
    from src.utils.config import settings
    const_process_levels = (
        (char.constitution_levels or None)
        if settings.constitution_process_enabled
        else None
    )
    const_bonuses = compute_constitution_bonuses(
        char.constitution_type, active_axis, char.body_realm,
        process_levels=const_process_levels,
    )
    # Per-element levels drive the linh_can stat scaling. NPC-style Characters
    # that don't carry ``linh_can_levels`` (built outside the DB pipeline) fall
    # back to level 1 per element from the bare ``linh_can`` list.
    lc_levels = dict(getattr(char, "linh_can_levels", {}) or {})
    if not lc_levels:
        lc_levels = {elem: 1 for elem in char.linh_can}
    lc_bonuses    = compute_linh_can_bonuses(lc_levels)

    # Khí Tu archetype payoff — rewards breadth (many high-level Linh Căn)
    # in the same data-driven shape as Hỗn Độn's all_passives_multiplier.
    # Gated on is_khi_tu (active_axis == "qi") so off-path players can't
    # dip into the bonus while focusing body or formation. Compound-offensive
    # stats are excluded for the same reason they're excluded from Hỗn Độn —
    # preventing one-shot damage against world bosses.
    from src.game.systems.cultivation import is_khi_tu
    from src.game.constants.balance import LINH_CAN_BREADTH_MAX_MULT
    from src.game.constants.linh_can import linh_can_breadth_multiplier
    _BREADTH_EXCLUDED_STATS: frozenset[str] = frozenset({
        "final_dmg_bonus", "true_dmg_pct", "cooldown_reduce", "final_dmg_reduce",
    })
    if is_khi_tu(active_axis):
        breadth_mult = linh_can_breadth_multiplier(lc_levels)
        if breadth_mult > 1.0:
            scaled: dict = {}
            for k, v in lc_bonuses.items():
                if isinstance(v, bool) or k in _BREADTH_EXCLUDED_STATS:
                    scaled[k] = v
                elif isinstance(v, (int, float)):
                    scaled[k] = type(v)(v * breadth_mult) if isinstance(v, int) else v * breadth_mult
                else:
                    scaled[k] = v
            lc_bonuses = scaled

    # Mộc Linh Căn passive: Hồi Xuân — always heals a flat % HP per turn in combat
    moc_regen = lc_effects.get_regen_bonus(char.linh_can, lc_levels.get("moc", 1))
    if moc_regen:
        lc_bonuses["hp_regen_pct"] = lc_bonuses.get("hp_regen_pct", 0.0) + moc_regen

    bonuses = merge_bonuses(form_bonuses, const_bonuses, lc_bonuses)
    # Flatten grouped author-friendly nested keys into the flat keys the
    # rest of this function already reads. Authors can declare:
    #   ``dot_dmg_bonus_by_kind: {"burn": 0.15}``
    # in JSON / passive_bonus / gem thresholds and it lands here as
    # ``burn_dmg_bonus: 0.15`` — same data, no churn on the read sites.
    _denest_grouped_bonuses(bonuses)

    # ── L6 Vạn Khí Giai Kim — equipment-stat amplifier (build-time pre-pass) ──
    # Thái Bạch Canh Kim Thể's milestone-6 stamps a config-only
    # ``equip_stat_amp_pct`` (×1.25) that amplifies ONLY the equipment-derived
    # portion of the stat sheet, baked static for the whole battle. Runs BEFORE
    # the equip-merge block below so every ``equip_stats.get(...)`` read sees the
    # amplified value; the key is popped from ``bonuses`` so it never lands as a
    # stat. Inert (no-op) when the body isn't equipped / the flag is off — the
    # key is simply absent, leaving ``equip_stats`` untouched.
    _equip_amp = float(bonuses.pop("equip_stat_amp_pct", 0.0))
    if _equip_amp > 0 and equip_stats:
        # Copy so we never mutate the caller's dict; amplify each numeric value
        # (bools and nested dicts pass through unscaled — they aren't flat
        # equipment magnitudes).
        equip_stats = {
            k: (v * (1.0 + _equip_amp)
                if isinstance(v, (int, float)) and not isinstance(v, bool)
                else v)
            for k, v in equip_stats.items()
        }

    # ── Base stats from cultivation ───────────────────────────────────────────
    hp_max   = compute_hp_max(char, bonuses)
    mp_max   = compute_mp_max(char, bonuses)
    atk      = compute_atk(char, bonuses)
    matk     = compute_matk(char, bonuses)
    def_stat = compute_def_stat(char, bonuses)

    # Realm power: flat damage bonus scaling with total cultivation stages
    total_stages = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm  * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    )
    realm_power_bonus = total_stages * REALM_POWER_BONUS_PER_STAGE

    # Equipment can contribute flat spd_bonus too (e.g. boot affixes).
    _equip_spd_bonus = int((equip_stats or {}).get("spd_bonus", 0))
    spd_base  = char.stats.spd + bonuses.get("spd_bonus", 0) + _equip_spd_bonus
    # ``spd_pct`` aggregates the constitution/formation/linh_can layer
    # (``bonuses``) and any equipment/skill-passive layer (``equip_stats``) —
    # equipped passives like Vạn Lý Truy Phong's +20 % spd land in equip_stats
    # via ``compute_skill_passive_stats`` and need this read to stick.
    _spd_pct_total = (
        float(bonuses.get("spd_pct", 0.0))
        + float((equip_stats or {}).get("spd_pct", 0.0))
    )
    spd_final = round(spd_base * (1.0 + _spd_pct_total))

    # ── Combat ratings ────────────────────────────────────────────────────────
    crit_rating     = char.stats.crit_rating     + bonuses.get("crit_rating", 0)
    crit_dmg_rating = char.stats.crit_dmg_rating + bonuses.get("crit_dmg_rating", 0)
    evasion_rating  = char.stats.evasion_rating  + bonuses.get("evasion_rating", 0)
    crit_res_rating = char.stats.crit_res_rating + bonuses.get("crit_res_rating", 0)
    accuracy_rating = bonuses.get("accuracy_rating", 0)

    final_dmg_bonus  = char.stats.final_dmg_bonus + bonuses.get("final_dmg_bonus", 0.0) + realm_power_bonus
    final_dmg_reduce = bonuses.get("final_dmg_reduce", 0.0)
    hp_regen_pct     = bonuses.get("hp_regen_pct", 0.0)
    hp_regen_flat    = int(bonuses.get("hp_regen_flat", 0))
    mp_regen_pct     = BASE_MP_REGEN_PCT + bonuses.get("mp_regen_pct", 0.0)
    mp_regen_flat    = int(bonuses.get("mp_regen_flat", 0))
    heal_pct         = bonuses.get("heal_pct", 0.0)
    cooldown_reduce  = char.stats.cooldown_reduce + bonuses.get("cooldown_reduce", 0.0)

    # On-hit / on-crit procs from formation thresholds
    burn_on_hit_pct   = bonuses.get("burn_on_hit_pct", 0.0)
    # Per-DoT-kind stack bonuses (burn/bleed/poison). JSON / equipment /
    # constitution data keep flat ``<kind>_stack_cap_bonus`` and
    # ``<kind>_per_stack_pct_bonus`` keys; aggregated into kind-keyed dicts
    # below so the equip merge / Combatant wiring stays a single loop. Final
    # ``stack_cap_bonuses`` (debuff-keyed) is built further down; per-stack
    # pcts feed the engine's scalar Combatant fields at construction.
    _STACK_DOT_KINDS = ("burn", "bleed", "poison")
    _stack_cap_bonus_by_kind: dict[str, int] = {
        k: int(bonuses.get(f"{k}_stack_cap_bonus", 0)) for k in _STACK_DOT_KINDS
    }
    _per_stack_pct_bonus_by_kind: dict[str, float] = {
        k: float(bonuses.get(f"{k}_per_stack_pct_bonus", 0.0)) for k in _STACK_DOT_KINDS
    }
    bonus_dmg_vs_burn = float(bonuses.get("bonus_dmg_vs_burn", 0.0))
    dot_can_crit      = bool(bonuses.get("dot_can_crit", False))
    # Per-element penetration — single dict-of-dicts pulled from bonuses.
    # Constitutions / Linh Căn / formations all emit ``element_pen``;
    # equip-side merge happens further below.
    element_pen: dict[str, float] = {
        e: float(v) for e, v in (bonuses.get("element_pen") or {}).items()
    }
    # Kim-build fields (bleed stack/per-stack pcts consolidated above in
    # ``_stack_cap_bonus_by_kind`` / ``_per_stack_pct_bonus_by_kind``)
    bleed_on_hit_pct        = float(bonuses.get("bleed_on_hit_pct", 0.0))
    poison_on_hit_pct       = float(bonuses.get("poison_on_hit_pct", 0.0))
    bleed_heal_reduce       = float(bonuses.get("bleed_heal_reduce", 0.0))
    # Conditional crit amps consolidated into ``crit_amp_vs`` (see CombatStats).
    # JSON keeps flat ``crit_rating_vs_<state>`` / ``crit_dmg_vs_<state>``
    # keys for backwards compat; translated into the nested dict here.
    _CRIT_AMP_STATES = ("bleed", "marked", "drained")
    crit_amp_vs: dict[str, dict[str, int]] = {}
    for _state in _CRIT_AMP_STATES:
        _r = int(bonuses.get(f"crit_rating_vs_{_state}", 0))
        _d = int(bonuses.get(f"crit_dmg_vs_{_state}", 0))
        if _r or _d:
            crit_amp_vs[_state] = {"rating": _r, "dmg": _d}
    true_dmg_pct            = float(bonuses.get("true_dmg_pct", 0.0))
    life_steal_pct           = float(bonuses.get("life_steal_pct", 0.0))
    crit_dmg_rating_to_dmg_pct = float(bonuses.get("crit_dmg_rating_to_dmg_pct", 0.0))
    # Moc-build fields
    dot_leech_pct            = float(bonuses.get("dot_leech_pct", 0.0))
    damage_from_heal_pct     = float(bonuses.get("damage_from_heal_pct", 0.0))
    damage_bonus_from_hp_pct = float(bonuses.get("damage_bonus_from_hp_pct", 0.0))
    # Thủy-build fields
    reflect_pct              = float(bonuses.get("reflect_pct", 0.0))
    reflect_applies_effects  = bool(bonuses.get("reflect_applies_effects", False))
    damage_bonus_from_mp_pct = float(bonuses.get("damage_bonus_from_mp_pct", 0.0))
    mp_leech_pct             = float(bonuses.get("mp_leech_pct", 0.0))
    mana_stack_cap_bonus     = int(bonuses.get("mana_stack_cap_bonus", 0))
    mana_stack_per_attack    = int(bonuses.get("mana_stack_per_attack", 0))
    mana_stack_dmg_bonus     = float(bonuses.get("mana_stack_dmg_bonus", 0.0))
    # Thổ-build fields
    shield_regen_pct             = float(bonuses.get("shield_regen_pct", 0.0))
    shield_regen_flat            = int(bonuses.get("shield_regen_flat", 0))
    shield_max_base              = int(bonuses.get("shield_max_base", 0))
    shield_max_flat              = int(bonuses.get("shield_max_flat", 0))
    shield_max_pct               = float(bonuses.get("shield_max_pct", 0.0))
    # Mirrors hp_flat_per_realm: lets shield-regen constitutions scale
    # their flat pool with cultivation so the regen has something to top
    # up at every tier without hand-tuning per-rarity numbers.
    shield_flat_per_realm        = int(bonuses.get("shield_flat_per_realm", 0))
    if shield_flat_per_realm:
        max_realm = max(char.body_realm, char.qi_realm, char.formation_realm)
        shield_max_flat += shield_flat_per_realm * max_realm
    hp_to_shield_pct             = float(bonuses.get("hp_to_shield_pct", 0.0))
    matk_from_shield_pct         = float(bonuses.get("matk_from_shield_pct", 0.0))
    atk_from_shield_pct          = float(bonuses.get("atk_from_shield_pct", 0.0))
    endure_threshold_pct         = float(bonuses.get("endure_threshold_pct", 0.0))
    endure_cooldown              = int(bonuses.get("endure_cooldown", 0))
    cleanse_heal_pct             = float(bonuses.get("cleanse_heal_pct", 0.0))
    cleanse_retaliate_dmg_pct    = float(bonuses.get("cleanse_retaliate_dmg_pct", 0.0))
    kill_buff_per_kill_pct       = float(bonuses.get("kill_buff_per_kill_pct", 0.0))
    kill_buff_cap                = int(bonuses.get("kill_buff_cap", 0))
    multi_strike_pct             = float(bonuses.get("multi_strike_pct", 0.0))
    multi_strike_dmg_pct         = float(bonuses.get("multi_strike_dmg_pct", 0.50))
    formation_echo_bonus         = max(0, int(bonuses.get("formation_echo_bonus", 0)))
    formation_skill_dmg_bonus    = max(0.0, float(bonuses.get("formation_skill_dmg_bonus", 0.0)))
    summon_limit_bonus           = max(0, int(bonuses.get("summon_limit_bonus", 0)))
    # Negative bonus shortens the recharge pause; clamp at 0 (instant regen).
    shield_recharge_delay_bonus  = int(bonuses.get("shield_recharge_delay_bonus", 0))
    damage_bonus_from_shield_pct = float(bonuses.get("damage_bonus_from_shield_pct", 0.0))
    thorn_pct                    = float(bonuses.get("thorn_pct", 0.0))
    thorn_from_shield            = bool(bonuses.get("thorn_from_shield", False))
    stun_on_hit_pct              = float(bonuses.get("stun_on_hit_pct", 0.0))
    # Per-constitution config flags (Kim killing-aura / crit-bleeder, Ám
    # shadow-mage, Quang silence-on-crit) — read from ``bonuses`` and merged
    # from ``equip_stats`` in one pass via the flag registry. ``equip_stats``
    # is already finalised here (the L6 equip-amp pre-pass is the only thing
    # that rebinds it, and it runs earlier), so merging now matches the old
    # two-stage read-then-equip-merge byte-for-byte. The result is splatted into
    # ``CombatStats(...)`` below. A new body adds ONE registry row, not four
    # edits. See ``_CONSTITUTION_FLAG_FIELDS``.
    _constitution_flags = _read_constitution_flags(bonuses, equip_stats)
    # Lôi-build fields
    shock_stack_cap_bonus        = int(bonuses.get("shock_stack_cap_bonus", 0))
    shock_per_stack_pct_bonus    = float(bonuses.get("shock_per_stack_pct_bonus", 0.0))
    shock_on_hit_pct             = float(bonuses.get("shock_on_hit_pct", 0.0))
    turn_steal_pct               = float(bonuses.get("turn_steal_pct", 0.0))
    # Poison-stack build fields (Mộc / Âm) — stack cap & per-stack pct
    # consolidated above in ``_stack_cap_bonus_by_kind`` / ``_per_stack_pct_bonus_by_kind``.
    # Phong-build fields
    mark_on_hit_pct              = float(bonuses.get("mark_on_hit_pct", 0.0))
    damage_bonus_from_evasion_pct= float(bonuses.get("damage_bonus_from_evasion_pct", 0.0))
    # ``crit_rating_vs_marked`` / ``crit_dmg_vs_marked`` and the Âm variants
    # are now consolidated into ``crit_amp_vs`` above.
    # Quang-build fields (``silence_on_crit_pct`` read via the flag registry)
    heal_reduce_on_hit_pct       = float(bonuses.get("heal_reduce_on_hit_pct", 0.0))
    blind_on_hit_pct             = float(bonuses.get("blind_on_hit_pct", 0.0))
    cleanse_on_turn_pct          = float(bonuses.get("cleanse_on_turn_pct", 0.0))
    barrier_on_cleanse           = bool(bonuses.get("barrier_on_cleanse", False))
    heal_can_crit                = bool(bonuses.get("heal_can_crit", False))
    # Âm-build fields
    soul_drain_on_hit_pct        = float(bonuses.get("soul_drain_on_hit_pct", 0.0))
    stat_steal_on_hit_pct        = float(bonuses.get("stat_steal_on_hit_pct", 0.0))
    # DoT-amplifier fields (cross-build). Per-kind amps consolidate into one
    # dict (mirrors element_pen / dmg_taken). Most authored data declares
    # ``dot_dmg_bonus_by_kind: {<kind>: X}`` (denested into flat keys at the
    # function entrance); equipment affix templates remain the one source
    # that still emits flat ``<kind>_dmg_bonus`` keys directly because
    # rolled-stat instances are stored that way in player inventories.
    dot_dmg_bonus                = float(bonuses.get("dot_dmg_bonus", 0.0))
    dot_dmg_bonus_by_kind: dict[str, float] = {}
    for _kind in ("burn", "bleed", "poison"):
        _v = float(bonuses.get(f"{_kind}_dmg_bonus", 0.0))
        if _v:
            dot_dmg_bonus_by_kind[_kind] = _v
    dot_scales_hp_pct            = bool(bonuses.get("dot_scales_hp_pct", False))
    solar_aura_pct               = float(bonuses.get("solar_aura_pct", 0.0))
    wither_aura_pct              = float(bonuses.get("wither_aura_pct", 0.0))
    phoenix_revive_pct           = float(bonuses.get("phoenix_revive_pct", 0.0))
    phoenix_revive_buff_pct      = float(bonuses.get("phoenix_revive_buff_pct", 0.0))
    stat_drain_aura_pct          = float(bonuses.get("stat_drain_aura_pct", 0.0))
    damage_defer_turns           = int(bonuses.get("damage_defer_turns", 0))
    damage_defer_pct             = float(bonuses.get("damage_defer_pct", 0.0))
    fortify_per_turn_pct         = float(bonuses.get("fortify_per_turn_pct", 0.0))
    fortify_stack_cap            = int(bonuses.get("fortify_stack_cap", 0))
    fortify_post_hit_dr_pct      = float(bonuses.get("fortify_post_hit_dr_pct", 0.0))
    sword_heart_per_stack_dr     = float(bonuses.get("sword_heart_per_stack_dr", 0.0))
    sword_summon_dmg_amp         = float(bonuses.get("sword_summon_dmg_amp", 0.0))
    loot_qty_bonus               = float(bonuses.get("loot_qty_bonus", 0.0))
    loot_luck_bonus              = float(bonuses.get("loot_luck_bonus", 0.0))
    bonus_base_dmg_per_self_hp_pct     = float(bonuses.get("bonus_base_dmg_per_self_hp_pct", 0.0))
    bonus_base_dmg_per_self_shield_pct = float(bonuses.get("bonus_base_dmg_per_self_shield_pct", 0.0))
    bonus_base_dmg_per_self_def_pct    = float(bonuses.get("bonus_base_dmg_per_self_def_pct", 0.0))
    def_applies_to_elemental_pct       = float(bonuses.get("def_applies_to_elemental_pct", 0.0))
    damage_taken_convert_pct: dict[str, float] = dict(bonuses.get("damage_taken_convert_pct", {}) or {})
    element_dmg_bonus: dict[str, float] = dict(bonuses.get("element_dmg_bonus", {}) or {})
    # Per-element max-res cap lifters. Two JSON shapes are accepted:
    #   "element_max_resist_bonus": {"hoa": 0.10, "kim": 0.05}     (preferred dict)
    #   "hoa_max_resist_bonus": 0.10, "kim_max_resist_bonus": 0.05  (flat keys)
    # Both flow into the same dict; combined at build time via additive merge.
    element_max_resist_bonus: dict[str, float] = dict(
        bonuses.get("element_max_resist_bonus", {}) or {}
    )
    for _elem in ALL_ELEMENTS:
        _ek = _elem.value
        _flat = float(bonuses.get(f"{_ek}_max_resist_bonus", 0.0))
        if _flat:
            element_max_resist_bonus[_ek] = (
                element_max_resist_bonus.get(_ek, 0.0) + _flat
            )
    # Per-element MP cost multiplier (extra cost on top of base 1.0×). Comes
    # from formations like Thiên Nhất Sinh Thủy Trận that amplify a single
    # element's MP outlay (and damage, via the base + mp_cost formula).
    element_mp_cost_mult: dict[str, float] = dict(bonuses.get("element_mp_cost_mult", {}) or {})
    # Cửu Khúc Hoàng Hà formation tunables — single dict (see CombatStats).
    # Followup chain power capped at 1.0 so misconfigured stacks can't push
    # past 100 % and double-trigger the formula.
    _ck_raw = bonuses.get("cuu_khuc") or {}
    cuu_khuc: dict = {
        "per_hit":            max(0, int(_ck_raw.get("per_hit", 0))),
        "atk_reduce_pct":     max(0.0, float(_ck_raw.get("atk_reduce_pct", 0.0))),
        "res_shred_pct":      max(0.0, float(_ck_raw.get("res_shred_pct", 0.0))),
        "followup_pct":       max(0.0, min(1.0, float(_ck_raw.get("followup_pct", 0.0)))),
        "followup_skill_key": str(_ck_raw.get("followup_skill_key", "")),
    }
    # Vạn Kiếp Lôi Ngục Trận — single dict pulled from bonuses. Every entry
    # is non-negative (clamps any misconfigured negative gem stack to 0) so
    # the formation can't end up healing the target or refunding > MP_max.
    _vk_raw = bonuses.get("loi_kiep_an") or {}
    loi_kiep_an: dict = {
        "per_cast_bonus":           max(0, int(_vk_raw.get("per_cast_bonus", 0))),
        "bolt_base_dmg":            max(0, int(_vk_raw.get("bolt_base_dmg", 0))),
        "bolt_matk_scale":          max(0.0, float(_vk_raw.get("bolt_matk_scale", 0.0))),
        "bolt_te_liet_chance":      max(0.0, min(1.0, float(_vk_raw.get("bolt_te_liet_chance", 0.0)))),
        "capstone_base_floor":      max(0, int(_vk_raw.get("capstone_base_floor", 0))),
        "capstone_base_per_stack":  max(0, int(_vk_raw.get("capstone_base_per_stack", 0))),
        "capstone_matk_floor":      max(0.0, float(_vk_raw.get("capstone_matk_floor", 0.0))),
        "capstone_matk_per_stack":  max(0.0, float(_vk_raw.get("capstone_matk_per_stack", 0.0))),
        "capstone_te_liet_chance":  max(0.0, min(1.0, float(_vk_raw.get("capstone_te_liet_chance", 0.0)))),
        "capstone_refund_mp_pct":   max(0.0, min(1.0, float(_vk_raw.get("capstone_refund_mp_pct", 0.0)))),
    }
    # Thiên Lôi Tru Tà Trận — single dict pulled from bonuses. Clamping
    # mirrors the loi_kiep_an block: counts/cooldowns are non-negative ints,
    # pcts are clamped to [0, 1] so misconfigured gems can't push past 100%.
    _tt_raw = bonuses.get("thien_loi_tru_ta") or {}
    thien_loi_tru_ta: dict = {
        "per_debuff_amp":         max(0.0, float(_tt_raw.get("per_debuff_amp", 0.0))),
        "debuff_count_cap":       max(0, int(_tt_raw.get("debuff_count_cap", 8))),
        "capstone_threshold":     max(1, int(_tt_raw.get("capstone_threshold", 5))),
        "dai_phan_cd":            max(0, int(_tt_raw.get("dai_phan_cd", 7))),
        "dai_phan_buff_strip":    max(0, int(_tt_raw.get("dai_phan_buff_strip", 1))),
        "low_hp_threshold_pct":   max(0.0, min(1.0, float(_tt_raw.get("low_hp_threshold_pct", 0.0)))),
        "low_hp_amp_pct":         max(0.0, float(_tt_raw.get("low_hp_amp_pct", 0.0))),
        "bolt_base":              max(0, int(_tt_raw.get("bolt_base", 0))),
        "bolt_base_per_debuff":   max(0, int(_tt_raw.get("bolt_base_per_debuff", 0))),
        "bolt_matk_scale":        max(0.0, float(_tt_raw.get("bolt_matk_scale", 0.0))),
        "bolt_matk_per_debuff":   max(0.0, float(_tt_raw.get("bolt_matk_per_debuff", 0.0))),
        "bolt_te_liet_chance_floor":     max(0.0, min(1.0, float(_tt_raw.get("bolt_te_liet_chance_floor", 0.0)))),
        "bolt_te_liet_chance_per_debuff": max(0.0, float(_tt_raw.get("bolt_te_liet_chance_per_debuff", 0.0))),
        "capstone_base":          max(0, int(_tt_raw.get("capstone_base", 0))),
        "capstone_base_per_debuff": max(0, int(_tt_raw.get("capstone_base_per_debuff", 0))),
        "capstone_matk_scale":    max(0.0, float(_tt_raw.get("capstone_matk_scale", 0.0))),
        "capstone_matk_per_debuff": max(0.0, float(_tt_raw.get("capstone_matk_per_debuff", 0.0))),
        "capstone_te_liet_chance": max(0.0, min(1.0, float(_tt_raw.get("capstone_te_liet_chance", 0.0)))),
        "capstone_te_liet_duration": max(0, int(_tt_raw.get("capstone_te_liet_duration", 2))),
    }
    # Thái Cực Âm Dương Lôi Đại Trận — single dict pulled from bonuses.
    # Capstone target HP-pct nerfed to 0.20 (was 0.50 in initial proposal) so
    # the fusion can't trivialize high-HP bosses on a single fire.
    _ad_raw = bonuses.get("thai_cuc_am_duong_loi") or {}
    thai_cuc_am_duong_loi: dict = {
        # Dương phase (attack pole) payload
        "duong_base":             max(0, int(_ad_raw.get("duong_base", 0))),
        "duong_matk_scale":       max(0.0, float(_ad_raw.get("duong_matk_scale", 0.0))),
        "duong_dmg_amp_pct":      max(0.0, float(_ad_raw.get("duong_dmg_amp_pct", 0.0))),
        "duong_shock_stacks":     max(0, int(_ad_raw.get("duong_shock_stacks", 1))),
        "duong_te_liet_chance":   max(0.0, min(1.0, float(_ad_raw.get("duong_te_liet_chance", 0.0)))),
        # Âm phase (utility pole) payload
        "am_shock_stacks":        max(0, int(_ad_raw.get("am_shock_stacks", 2))),
        "am_hp_restore_pct":      max(0.0, min(1.0, float(_ad_raw.get("am_hp_restore_pct", 0.0)))),
        "am_mp_restore_pct":      max(0.0, min(1.0, float(_ad_raw.get("am_mp_restore_pct", 0.0)))),
        "am_buff_extend_cap":     max(0, int(_ad_raw.get("am_buff_extend_cap", 6))),
        "am_apply_te_nguyen_luc": bool(_ad_raw.get("am_apply_te_nguyen_luc", True)),
        # Thái Cực Lưỡng Nghi Lôi fusion (capstone)
        "fusion_base":            max(0, int(_ad_raw.get("fusion_base", 0))),
        "fusion_matk_scale":      max(0.0, float(_ad_raw.get("fusion_matk_scale", 0.0))),
        "fusion_target_hp_pct":   max(0.0, min(1.0, float(_ad_raw.get("fusion_target_hp_pct", 0.0)))),
        "fusion_shock_bonus_per_stack": max(0, int(_ad_raw.get("fusion_shock_bonus_per_stack", 0))),
        "fusion_te_liet_duration": max(0, int(_ad_raw.get("fusion_te_liet_duration", 3))),
        "fusion_cd":              max(0, int(_ad_raw.get("fusion_cd", 8))),
        "khi_cap":                max(1, int(_ad_raw.get("khi_cap", 5))),
        # 10-gem tier: both poles fire every tick (duality collapses).
        "dual_phase_fire":        bool(_ad_raw.get("dual_phase_fire", False)),
    }
    # Cửu Long Thần Hỏa Trận — single dict pulled from bonuses.
    _cl_raw = bonuses.get("cuu_long") or {}
    cuu_long: dict = {
        "dmg_pct_bonus":   max(0.0, float(_cl_raw.get("dmg_pct_bonus", 0.0))),
        "crit_rating":     max(0, int(_cl_raw.get("crit_rating", 0))),
        "crit_dmg_rating": max(0, int(_cl_raw.get("crit_dmg_rating", 0))),
        "stun_chance":     max(0.0, min(1.0, float(_cl_raw.get("stun_chance", 0.0)))),
        "finisher_pct":    max(0.0, float(_cl_raw.get("finisher_pct", 0.0))),
    }
    # Xích Luyện Tỏa Hồn Trận — single dict pulled from bonuses (mirrors
    # element_pen / dmg_taken). Clamp each delta to (-1.0, +∞) so a
    # misconfigured negative stack can't drive a debuff past 100% reduction.
    toa_hon_amp: dict[str, float] = {
        k: max(-1.0, float(v))
        for k, v in (bonuses.get("toa_hon_amp") or {}).items()
    }
    # Per-element "damage taken" amps — single dict pulled from bonuses
    # (mirrors element_pen). Floor each entry at 0 so a misconfigured
    # negative gem stack can't end up healing a target on the amp step.
    dmg_taken: dict[str, float] = {
        e: max(0.0, float(v))
        for e, v in (bonuses.get("dmg_taken") or {}).items()
    }
    slow_on_hit_pct   = bonuses.get("slow_on_hit_pct", 0.0)
    paralysis_on_crit = bonuses.get("paralysis_on_crit", False)
    freeze_on_skill_chance = float(bonuses.get("freeze_on_skill_chance", 0.0))
    poison_immunity   = bonuses.get("poison_immunity", False)
    debuff_immune_pct = bonuses.get("debuff_immune_pct", 0.0)

    # ── Resistances ───────────────────────────────────────────────────────────
    resistances: dict[str, float] = {
        e.value: getattr(char.stats, RESISTANCE_KEYS[e]) for e in ALL_ELEMENTS
    }

    res_all = bonuses.get("res_all", 0.0)
    if res_all:
        for elem in resistances:
            resistances[elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances[elem] + res_all))

    # Per-element resistance pickups (constitutions and equipment can raise
    # individual element resistances via ``res_hoa``, ``res_kim``, etc.).
    for elem in resistances:
        bump = float(bonuses.get(f"res_{elem}", 0.0))
        if bump:
            resistances[elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances[elem] + bump))

    form_elem = form_bonuses.get("_formation_element")
    res_elem  = form_bonuses.get("res_element", 0.0)
    if form_elem and res_elem:
        resistances[form_elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances.get(form_elem, 0.0) + res_elem))

    # ── Equipment bonuses (applied last) ──────────────────────────────────────
    if equip_stats:
        # Unique items declare ``passive_bonus`` with nested grouped keys
        # (dot_dmg_bonus_by_kind, crit_amp_vs, …). Denest in place so the
        # flat ``.get("burn_dmg_bonus", 0.0)`` reads below pick them up.
        _denest_grouped_bonuses(equip_stats)
        atk             += int(equip_stats.get("atk", 0))
        matk            += int(equip_stats.get("matk", 0))
        def_stat        += int(equip_stats.get("def_stat", 0))
        crit_rating     += int(equip_stats.get("crit_rating", 0))
        crit_dmg_rating += int(equip_stats.get("crit_dmg_rating", 0))
        evasion_rating  += int(equip_stats.get("evasion_rating", 0))
        crit_res_rating += int(equip_stats.get("crit_res_rating", 0))
        accuracy_rating += int(equip_stats.get("accuracy_rating", 0))
        final_dmg_bonus  += equip_stats.get("final_dmg_bonus", 0.0)
        final_dmg_reduce  = min(
            MAX_FINAL_DMG_REDUCE,
            final_dmg_reduce + equip_stats.get("final_dmg_reduce", 0.0),
        )
        hp_regen_pct  += equip_stats.get("hp_regen_pct", 0.0)
        hp_regen_flat += int(equip_stats.get("hp_regen_flat", 0))
        mp_regen_pct  += equip_stats.get("mp_regen_pct", 0.0)
        mp_regen_flat += int(equip_stats.get("mp_regen_flat", 0))
        hp_max += int(equip_stats.get("hp_max", 0))
        mp_max += int(equip_stats.get("mp_max", 0))

        eq_res_all = equip_stats.get("res_all", 0.0)
        if eq_res_all:
            for elem in resistances:
                resistances[elem] = min(MAX_ELEMENTAL_RES, resistances[elem] + eq_res_all)
        for elem in resistances:
            eq_bump = float(equip_stats.get(f"res_{elem}", 0.0))
            if eq_bump:
                resistances[elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances[elem] + eq_bump))

        # Passive bonuses from unique items (on-hit procs, immunities, etc.)
        burn_on_hit_pct   += equip_stats.get("burn_on_hit_pct", 0.0)
        # Per-kind stack cap / per-stack pct equip merges — single loop over
        # the burn/bleed/poison kinds.
        for _kind in _STACK_DOT_KINDS:
            _stack_cap_bonus_by_kind[_kind] += int(
                equip_stats.get(f"{_kind}_stack_cap_bonus", 0)
            )
            _per_stack_pct_bonus_by_kind[_kind] += float(
                equip_stats.get(f"{_kind}_per_stack_pct_bonus", 0.0)
            )
        bonus_dmg_vs_burn += float(equip_stats.get("bonus_dmg_vs_burn", 0.0))
        dot_can_crit       = dot_can_crit or bool(equip_stats.get("dot_can_crit", False))
        # Per-element penetration dict from equip — additive merge.
        for _e, _v in (equip_stats.get("element_pen") or {}).items():
            element_pen[_e] = element_pen.get(_e, 0.0) + float(_v)
        # Kim-build fields (bleed stack cap / per-stack pct merged above
        # via ``_stack_cap_bonus_by_kind`` / ``_per_stack_pct_bonus_by_kind``)
        bleed_on_hit_pct        += float(equip_stats.get("bleed_on_hit_pct", 0.0))
        poison_on_hit_pct       += float(equip_stats.get("poison_on_hit_pct", 0.0))
        bleed_heal_reduce       += float(equip_stats.get("bleed_heal_reduce", 0.0))
        # Conditional crit amps — merge bleed/marked/drained from equip into
        # the consolidated crit_amp_vs dict (see CombatStats). Equipment
        # affixes keep flat keys; translated here.
        for _state in _CRIT_AMP_STATES:
            _r = int(equip_stats.get(f"crit_rating_vs_{_state}", 0))
            _d = int(equip_stats.get(f"crit_dmg_vs_{_state}", 0))
            if _r or _d:
                _existing = crit_amp_vs.setdefault(_state, {"rating": 0, "dmg": 0})
                _existing["rating"] = _existing.get("rating", 0) + _r
                _existing["dmg"]    = _existing.get("dmg", 0) + _d
        true_dmg_pct            += float(equip_stats.get("true_dmg_pct", 0.0))
        life_steal_pct           += float(equip_stats.get("life_steal_pct", 0.0))
        crit_dmg_rating_to_dmg_pct += float(equip_stats.get("crit_dmg_rating_to_dmg_pct", 0.0))
        # Moc-build fields
        dot_leech_pct            += float(equip_stats.get("dot_leech_pct", 0.0))
        damage_from_heal_pct     += float(equip_stats.get("damage_from_heal_pct", 0.0))
        damage_bonus_from_hp_pct += float(equip_stats.get("damage_bonus_from_hp_pct", 0.0))
        # Thủy-build fields
        reflect_pct              += float(equip_stats.get("reflect_pct", 0.0))
        reflect_applies_effects   = reflect_applies_effects or bool(equip_stats.get("reflect_applies_effects", False))
        damage_bonus_from_mp_pct += float(equip_stats.get("damage_bonus_from_mp_pct", 0.0))
        mp_leech_pct             += float(equip_stats.get("mp_leech_pct", 0.0))
        mana_stack_cap_bonus     += int(equip_stats.get("mana_stack_cap_bonus", 0))
        mana_stack_per_attack    += int(equip_stats.get("mana_stack_per_attack", 0))
        mana_stack_dmg_bonus     += float(equip_stats.get("mana_stack_dmg_bonus", 0.0))
        # Thổ-build fields
        shield_regen_pct             += float(equip_stats.get("shield_regen_pct", 0.0))
        shield_regen_flat            += int(equip_stats.get("shield_regen_flat", 0))
        shield_max_base              += int(equip_stats.get("shield_max_base", 0))
        shield_max_flat              += int(equip_stats.get("shield_max_flat", 0))
        shield_max_pct               += float(equip_stats.get("shield_max_pct", 0.0))
        eq_shield_per_realm = int(equip_stats.get("shield_flat_per_realm", 0))
        if eq_shield_per_realm:
            max_realm = max(char.body_realm, char.qi_realm, char.formation_realm)
            shield_max_flat += eq_shield_per_realm * max_realm
        hp_to_shield_pct             += float(equip_stats.get("hp_to_shield_pct", 0.0))
        matk_from_shield_pct         += float(equip_stats.get("matk_from_shield_pct", 0.0))
        atk_from_shield_pct          += float(equip_stats.get("atk_from_shield_pct", 0.0))
        endure_threshold_pct         = max(endure_threshold_pct, float(equip_stats.get("endure_threshold_pct", 0.0)))
        endure_cooldown              = max(endure_cooldown, int(equip_stats.get("endure_cooldown", 0)))
        cleanse_heal_pct             += float(equip_stats.get("cleanse_heal_pct", 0.0))
        cleanse_retaliate_dmg_pct    += float(equip_stats.get("cleanse_retaliate_dmg_pct", 0.0))
        kill_buff_per_kill_pct       = max(kill_buff_per_kill_pct, float(equip_stats.get("kill_buff_per_kill_pct", 0.0)))
        kill_buff_cap                = max(kill_buff_cap, int(equip_stats.get("kill_buff_cap", 0)))
        multi_strike_pct             += float(equip_stats.get("multi_strike_pct", 0.0))
        multi_strike_dmg_pct         = max(multi_strike_dmg_pct, float(equip_stats.get("multi_strike_dmg_pct", 0.0)))
        damage_bonus_from_shield_pct += float(equip_stats.get("damage_bonus_from_shield_pct", 0.0))
        thorn_pct                    += float(equip_stats.get("thorn_pct", 0.0))
        thorn_from_shield             = thorn_from_shield or bool(equip_stats.get("thorn_from_shield", False))
        stun_on_hit_pct              += float(equip_stats.get("stun_on_hit_pct", 0.0))
        # Per-constitution config flags (Kim / Ám / Quang silence) are merged
        # from ``equip_stats`` inside ``_read_constitution_flags`` above — no
        # per-flag equip line needed here. See ``_CONSTITUTION_FLAG_FIELDS``.
        # Lôi-build fields
        shock_stack_cap_bonus        += int(equip_stats.get("shock_stack_cap_bonus", 0))
        shock_per_stack_pct_bonus    += float(equip_stats.get("shock_per_stack_pct_bonus", 0.0))
        shock_on_hit_pct             += float(equip_stats.get("shock_on_hit_pct", 0.0))
        turn_steal_pct               += float(equip_stats.get("turn_steal_pct", 0.0))
        # Poison-stack build fields — stack cap / per-stack pct merged above
        # in the per-kind loop.
        # Phong-build fields
        mark_on_hit_pct              += float(equip_stats.get("mark_on_hit_pct", 0.0))
        damage_bonus_from_evasion_pct+= float(equip_stats.get("damage_bonus_from_evasion_pct", 0.0))
        # crit_rating_vs_marked / crit_dmg_vs_marked merged into crit_amp_vs above.
        # Quang-build fields (``silence_on_crit_pct`` merged via the flag registry)
        heal_reduce_on_hit_pct       += float(equip_stats.get("heal_reduce_on_hit_pct", 0.0))
        blind_on_hit_pct             += float(equip_stats.get("blind_on_hit_pct", 0.0))
        cleanse_on_turn_pct          += float(equip_stats.get("cleanse_on_turn_pct", 0.0))
        barrier_on_cleanse            = barrier_on_cleanse or bool(equip_stats.get("barrier_on_cleanse", False))
        heal_can_crit                 = heal_can_crit or bool(equip_stats.get("heal_can_crit", False))
        # Âm-build fields
        soul_drain_on_hit_pct        += float(equip_stats.get("soul_drain_on_hit_pct", 0.0))
        stat_steal_on_hit_pct        += float(equip_stats.get("stat_steal_on_hit_pct", 0.0))
        # crit_rating_vs_drained merged into crit_amp_vs above.
        # DoT-amplifier fields. Equipment affixes keep the flat ``<kind>_dmg_bonus``
        # key convention; merged into the per-kind dict here.
        dot_dmg_bonus                += float(equip_stats.get("dot_dmg_bonus", 0.0))
        for _kind in ("burn", "bleed", "poison"):
            _v = float(equip_stats.get(f"{_kind}_dmg_bonus", 0.0))
            if _v:
                dot_dmg_bonus_by_kind[_kind] = dot_dmg_bonus_by_kind.get(_kind, 0.0) + _v
        dot_scales_hp_pct             = dot_scales_hp_pct or bool(equip_stats.get("dot_scales_hp_pct", False))
        solar_aura_pct               += float(equip_stats.get("solar_aura_pct", 0.0))
        wither_aura_pct              += float(equip_stats.get("wither_aura_pct", 0.0))
        phoenix_revive_pct           = max(phoenix_revive_pct, float(equip_stats.get("phoenix_revive_pct", 0.0)))
        phoenix_revive_buff_pct      = max(phoenix_revive_buff_pct, float(equip_stats.get("phoenix_revive_buff_pct", 0.0)))
        stat_drain_aura_pct          = max(stat_drain_aura_pct, float(equip_stats.get("stat_drain_aura_pct", 0.0)))
        damage_defer_turns           = max(damage_defer_turns, int(equip_stats.get("damage_defer_turns", 0)))
        damage_defer_pct             = max(damage_defer_pct, float(equip_stats.get("damage_defer_pct", 0.0)))
        fortify_per_turn_pct         = max(fortify_per_turn_pct, float(equip_stats.get("fortify_per_turn_pct", 0.0)))
        fortify_stack_cap            = max(fortify_stack_cap, int(equip_stats.get("fortify_stack_cap", 0)))
        fortify_post_hit_dr_pct      = max(fortify_post_hit_dr_pct, float(equip_stats.get("fortify_post_hit_dr_pct", 0.0)))
        sword_heart_per_stack_dr    += float(equip_stats.get("sword_heart_per_stack_dr", 0.0))
        sword_summon_dmg_amp        += float(equip_stats.get("sword_summon_dmg_amp", 0.0))
        loot_qty_bonus              += float(equip_stats.get("loot_qty_bonus", 0.0))
        loot_luck_bonus             += float(equip_stats.get("loot_luck_bonus", 0.0))
        for elem, val in (equip_stats.get("damage_taken_convert_pct") or {}).items():
            damage_taken_convert_pct[elem] = damage_taken_convert_pct.get(elem, 0.0) + float(val)
        for elem, val in (equip_stats.get("element_dmg_bonus") or {}).items():
            element_dmg_bonus[elem] = element_dmg_bonus.get(elem, 0.0) + float(val)
        # ``element_max_resist_bonus`` from equipment — dict form merges per-
        # element; flat ``<elem>_max_resist_bonus`` keys roll up to the same dict.
        for elem, val in (equip_stats.get("element_max_resist_bonus") or {}).items():
            element_max_resist_bonus[elem] = element_max_resist_bonus.get(elem, 0.0) + float(val)
        for _elem in ALL_ELEMENTS:
            _ek = _elem.value
            _flat = float(equip_stats.get(f"{_ek}_max_resist_bonus", 0.0))
            if _flat:
                element_max_resist_bonus[_ek] = (
                    element_max_resist_bonus.get(_ek, 0.0) + _flat
                )
        # Affixes contribute via flat keys: ``element_dmg_all`` adds to every
        # element; ``element_dmg_<elem>`` adds to that one element only.
        # Both fold into the same ``element_dmg_bonus`` dict the combat
        # pipeline reads (see ``combat_hit.py``).
        eq_dmg_all = float(equip_stats.get("element_dmg_all", 0.0))
        if eq_dmg_all:
            for _e in ALL_ELEMENTS:
                element_dmg_bonus[_e.value] = element_dmg_bonus.get(_e.value, 0.0) + eq_dmg_all
        for _e in ALL_ELEMENTS:
            bump = float(equip_stats.get(f"element_dmg_{_e.value}", 0.0))
            if bump:
                element_dmg_bonus[_e.value] = element_dmg_bonus.get(_e.value, 0.0) + bump
        slow_on_hit_pct   += equip_stats.get("slow_on_hit_pct", 0.0)
        paralysis_on_crit  = paralysis_on_crit or bool(equip_stats.get("paralysis_on_crit", False))
        freeze_on_skill_chance += float(equip_stats.get("freeze_on_skill_chance", 0.0))
        poison_immunity    = poison_immunity   or bool(equip_stats.get("poison_immunity", False))
        debuff_immune_pct += equip_stats.get("debuff_immune_pct", 0.0)
        heal_pct          += equip_stats.get("heal_pct", 0.0)
        cooldown_reduce   += equip_stats.get("cooldown_reduce", 0.0)

    # Clamp cumulative CDR after every source has contributed. Constitution +
    # equipment + gem stacks can push past 200 % on end-game Thể Tu builds;
    # the cap keeps the per-cast accumulator from refunding faster than the
    # economy intends.
    cooldown_reduce = min(cooldown_reduce, COOLDOWN_REDUCE_CAP)

    mp_max, mp_reserved, reserve_pct = _apply_formation_mp_reserve(
        mp_max, form_bonuses, active_formations, formation_stages,
    )
    hp_max, shield_max_base, hp_to_shield_pct = _apply_hp_to_shield(
        hp_max, shield_max_base, hp_to_shield_pct,
    )
    spd_final, def_stat, atk = _apply_pill_buffs(
        char, spd_final, def_stat, atk, element_dmg_bonus,
    )
    final_dmg_bonus, hp_regen_pct = _apply_toxicity_penalty(
        char, final_dmg_bonus, hp_regen_pct,
    )

    # ── Player soft-cap pass ──────────────────────────────────────────────
    # After all gear / constitution / Linh Căn contributions have folded in,
    # clamp each element's static resistance to the player's effective cap:
    #   cap = min(MAX_ELEMENTAL_RES, RES_SOFT_CAP + element_max_resist_bonus[elem])
    # Buff-driven additions at runtime are clamped by the same formula via
    # ``effective_res_cap``.
    from src.game.constants.balance import RES_SOFT_CAP
    for elem in resistances:
        soft = RES_SOFT_CAP + float(element_max_resist_bonus.get(elem, 0.0))
        cap = min(MAX_ELEMENTAL_RES, soft)
        if resistances[elem] > cap:
            resistances[elem] = cap

    # Stack-cap bonuses dict — gear/constitution/Linh Căn flat caps route
    # through the same generic ``stack_cap_bonuses`` channel keyed by debuff
    # effect key. burn/bleed/poison feed in from the consolidated
    # ``_stack_cap_bonus_by_kind`` dict above; shock keeps its own scalar
    # since its build path differs from the burn/bleed/poison family.
    _STACK_KIND_TO_DEBUFF: dict[str, str] = {
        "burn":   "DebuffThieuDot",
        "bleed":  "DebuffChayMau",
        "poison": "DebuffDocTo",
    }
    stack_cap_bonuses: dict[str, int] = {}
    for _kind, _debuff_key in _STACK_KIND_TO_DEBUFF.items():
        _v = _stack_cap_bonus_by_kind.get(_kind, 0)
        if _v:
            stack_cap_bonuses[_debuff_key] = _v
    if shock_stack_cap_bonus:
        stack_cap_bonuses["DebuffSocDien"] = shock_stack_cap_bonus

    return CombatStats(
        hp_max=hp_max,
        mp_max=mp_max,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        spd=spd_final,
        crit_rating=crit_rating,
        crit_dmg_rating=crit_dmg_rating,
        evasion_rating=evasion_rating,
        crit_res_rating=crit_res_rating,
        accuracy_rating=accuracy_rating,
        final_dmg_bonus=final_dmg_bonus,
        final_dmg_reduce=final_dmg_reduce,
        hp_regen_pct=hp_regen_pct,
        hp_regen_flat=hp_regen_flat,
        mp_regen_pct=mp_regen_pct,
        mp_regen_flat=mp_regen_flat,
        heal_pct=heal_pct,
        cooldown_reduce=cooldown_reduce,
        burn_on_hit_pct=burn_on_hit_pct,
        slow_on_hit_pct=slow_on_hit_pct,
        paralysis_on_crit=paralysis_on_crit,
        freeze_on_skill_chance=freeze_on_skill_chance,
        poison_immunity=poison_immunity,
        debuff_immune_pct=debuff_immune_pct,
        burn_per_stack_pct=_BURN_PCT_DEFAULT + _per_stack_pct_bonus_by_kind.get("burn", 0.0),
        bonus_dmg_vs_burn=bonus_dmg_vs_burn,
        dot_can_crit=dot_can_crit,
        bleed_per_stack_pct=_BLEED_PCT_DEFAULT + _per_stack_pct_bonus_by_kind.get("bleed", 0.0),
        bleed_on_hit_pct=bleed_on_hit_pct,
        poison_on_hit_pct=poison_on_hit_pct,
        bleed_heal_reduce=bleed_heal_reduce,
        crit_amp_vs=crit_amp_vs,
        true_dmg_pct=true_dmg_pct,
        life_steal_pct=life_steal_pct,
        crit_dmg_rating_to_dmg_pct=crit_dmg_rating_to_dmg_pct,
        dot_leech_pct=dot_leech_pct,
        damage_from_heal_pct=damage_from_heal_pct,
        damage_bonus_from_hp_pct=damage_bonus_from_hp_pct,
        reflect_pct=reflect_pct,
        reflect_applies_effects=reflect_applies_effects,
        damage_bonus_from_mp_pct=damage_bonus_from_mp_pct,
        mp_leech_pct=mp_leech_pct,
        mana_stack_cap=DEFAULT_MANA_STACK_CAP + mana_stack_cap_bonus,
        mana_stack_per_attack=mana_stack_per_attack,
        mana_stack_dmg_bonus=mana_stack_dmg_bonus,
        shield_regen_pct=shield_regen_pct,
        shield_regen_flat=shield_regen_flat,
        shield_max_base=max(0, shield_max_base),
        shield_max_flat=max(0, shield_max_flat),
        shield_max_pct=max(0.0, shield_max_pct),
        hp_to_shield_pct=hp_to_shield_pct,
        matk_from_shield_pct=max(0.0, matk_from_shield_pct),
        atk_from_shield_pct=max(0.0, atk_from_shield_pct),
        endure_threshold_pct=max(0.0, min(0.95, endure_threshold_pct)),
        endure_cooldown=max(0, endure_cooldown),
        cleanse_heal_pct=max(0.0, cleanse_heal_pct),
        cleanse_retaliate_dmg_pct=max(0.0, cleanse_retaliate_dmg_pct),
        kill_buff_per_kill_pct=max(0.0, kill_buff_per_kill_pct),
        kill_buff_cap=max(0, kill_buff_cap),
        multi_strike_pct=max(0.0, min(1.0, multi_strike_pct)),
        multi_strike_dmg_pct=max(0.0, min(2.0, multi_strike_dmg_pct)),
        formation_echo_bonus=formation_echo_bonus,
        formation_skill_dmg_bonus=formation_skill_dmg_bonus,
        summon_limit_bonus=summon_limit_bonus,
        shield_recharge_delay=max(0, DEFAULT_SHIELD_RECHARGE_DELAY + shield_recharge_delay_bonus),
        damage_bonus_from_shield_pct=damage_bonus_from_shield_pct,
        thorn_pct=thorn_pct,
        thorn_from_shield=thorn_from_shield,
        stun_on_hit_pct=stun_on_hit_pct,
        # Per-constitution config flags (Kim / Ám / Quang silence) — splatted
        # from the flag registry's single read+merge pass. See
        # ``_CONSTITUTION_FLAG_FIELDS`` / ``_read_constitution_flags``.
        **_constitution_flags,
        shock_per_stack_pct=_SHOCK_PCT_DEFAULT + shock_per_stack_pct_bonus,
        shock_on_hit_pct=shock_on_hit_pct,
        turn_steal_pct=turn_steal_pct,
        poison_per_stack_pct=_POISON_PCT_DEFAULT + _per_stack_pct_bonus_by_kind.get("poison", 0.0),
        mark_on_hit_pct=mark_on_hit_pct,
        damage_bonus_from_evasion_pct=damage_bonus_from_evasion_pct,
        heal_reduce_on_hit_pct=heal_reduce_on_hit_pct,
        blind_on_hit_pct=blind_on_hit_pct,
        cleanse_on_turn_pct=cleanse_on_turn_pct,
        barrier_on_cleanse=barrier_on_cleanse,
        heal_can_crit=heal_can_crit,
        soul_drain_on_hit_pct=soul_drain_on_hit_pct,
        stat_steal_on_hit_pct=stat_steal_on_hit_pct,
        dot_dmg_bonus=dot_dmg_bonus,
        dot_dmg_bonus_by_kind=dot_dmg_bonus_by_kind,
        dot_scales_hp_pct=dot_scales_hp_pct,
        solar_aura_pct=solar_aura_pct,
        wither_aura_pct=wither_aura_pct,
        phoenix_revive_pct=phoenix_revive_pct,
        phoenix_revive_buff_pct=phoenix_revive_buff_pct,
        stat_drain_aura_pct=stat_drain_aura_pct,
        damage_defer_turns=damage_defer_turns,
        damage_defer_pct=damage_defer_pct,
        fortify_per_turn_pct=fortify_per_turn_pct,
        fortify_stack_cap=fortify_stack_cap,
        fortify_post_hit_dr_pct=fortify_post_hit_dr_pct,
        sword_heart_per_stack_dr=sword_heart_per_stack_dr,
        sword_summon_dmg_amp=sword_summon_dmg_amp,
        loot_qty_bonus=loot_qty_bonus,
        loot_luck_bonus=loot_luck_bonus,
        bonus_base_dmg_per_self_hp_pct=bonus_base_dmg_per_self_hp_pct,
        bonus_base_dmg_per_self_shield_pct=bonus_base_dmg_per_self_shield_pct,
        bonus_base_dmg_per_self_def_pct=bonus_base_dmg_per_self_def_pct,
        def_applies_to_elemental_pct=def_applies_to_elemental_pct,
        damage_taken_convert_pct=damage_taken_convert_pct,
        element_dmg_bonus=element_dmg_bonus,
        element_mp_cost_mult=element_mp_cost_mult,
        cuu_khuc=cuu_khuc,
        loi_kiep_an=loi_kiep_an,
        thien_loi_tru_ta=thien_loi_tru_ta,
        thai_cuc_am_duong_loi=thai_cuc_am_duong_loi,
        cuu_long=cuu_long,
        toa_hon_amp=toa_hon_amp,
        dmg_taken=dmg_taken,
        element_pen=element_pen,
        mp_reserved=mp_reserved,
        mp_reserve_pct=reserve_pct,
        resistances=resistances,
        stack_cap_bonuses=stack_cap_bonuses,
        element_max_resist_bonus=element_max_resist_bonus,
    )
