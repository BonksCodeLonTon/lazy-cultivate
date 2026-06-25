"""Derived combat-stat data model + per-constitution config-flag registry.

Holds the ``CombatStats`` DTO (every derived stat for one character) plus the
``_CONSTITUTION_FLAG_FIELDS`` registry and ``_read_constitution_flags`` reader
that fold per-body config flags onto it. Split out of ``character_stats`` so
that module can stay focused on the ``compute_combat_stats`` computation;
``character_stats`` re-exports ``CombatStats`` (and the per-stack DoT defaults)
for backward compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.game.constants.balance import (
    DEFAULT_MANA_STACK_CAP,
    DEFAULT_SHIELD_RECHARGE_DELAY,
)
from src.game.systems.combat.helpers import meta_per_stack_pct


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
    # Thiên Thủy Thánh Thể (Thủy holy-spring sustain tank) — config flags.
    # L9 ``res_thuy``/``final_dmg_reduce`` are REAL stats (handled by the normal
    # resistance/aggregate path, NOT here). These are the mechanic flags consumed
    # by apply_reactive_damage (L3 convert/reflect), _apply_heal (L6 cleanse +
    # Tịnh Hóa), and the casting defender block (L9 abyss swallow). Runtime
    # counter ``tt_tinh_hoa_stacks`` is Combatant-only and excluded.
    ("tt_dmg_convert_heal_pct",          "tt_dmg_convert_heal_pct",          float),
    ("tt_reflect_remainder_pct",         "tt_reflect_remainder_pct",         float),
    ("tt_heal_cleanse_chance",           "tt_heal_cleanse_chance",           float),
    ("tt_tinh_hoa_cap",                  "tt_tinh_hoa_cap",                  int),
    ("tt_tinh_hoa_per_stack_cleanse",    "tt_tinh_hoa_per_stack_cleanse",    float),
    ("tt_tinh_hoa_mp_on_heal_pct",       "tt_tinh_hoa_mp_on_heal_pct",       float),
    ("tt_abyss_threshold",               "tt_abyss_threshold",               int),
    ("tt_abyss_reduce_pct",              "tt_abyss_reduce_pct",              float),
    ("tt_abyss_reflect_pct",             "tt_abyss_reflect_pct",             float),
    # Lưu Ly Thuẫn Thân Thể (universal shield-only aegis body) — config flags.
    # ``shield_only_body`` + ``shield_from_hp_max_pct`` sit in the FLAT
    # stat_bonuses so the core mechanic (hp_max→1, shield absorbs everything) is
    # always-on when equipped; the conversion itself happens in compute_combat_stats.
    # L6 reuses the existing ``damage_bonus_from_shield_pct`` field (no row here).
    # ``aegis_reform_just_triggered`` is a runtime flag (Combatant-only, excluded).
    ("shield_only_body",                 "shield_only_body",                 bool),
    ("shield_from_hp_max_pct",           "shield_from_hp_max_pct",           float),
    ("heal_to_shield_pct",               "heal_to_shield_pct",               float),
    ("aegis_reform_charges",             "aegis_reform_charges",             int),
    ("aegis_reform_shield_pct",          "aegis_reform_shield_pct",          float),
    # Liệt Diễm Phần Thiên Thể (Hỏa escalating fire nuker) — config flags.
    # L1 per-turn ramp (periodic increments lietdiem_burn_stacks; the buff's
    # scaling_rules convert it to matk_pct + crit_rating). L6 fire-absorb on hoa
    # hits (apply_reactive_damage). L9 avatar cadence (periodic stamps the burst
    # buff). Runtime counters (lietdiem_burn_stacks / lietdiem_van_hoa_stacks /
    # lietdiem_avatar_counter) are Combatant-only and excluded.
    ("lietdiem_burn_per_turn",           "lietdiem_burn_per_turn",           int),
    ("lietdiem_burn_cap",                "lietdiem_burn_cap",                int),
    ("lietdiem_van_hoa_absorb",          "lietdiem_van_hoa_absorb",          bool),
    ("lietdiem_van_hoa_cap",             "lietdiem_van_hoa_cap",             int),
    ("lietdiem_avatar_enabled",          "lietdiem_avatar_enabled",          bool),
    ("lietdiem_avatar_interval",         "lietdiem_avatar_interval",         int),
    ("lietdiem_avatar_duration",         "lietdiem_avatar_duration",         int),
    # Niết Bàn Bất Diệt Thể (Hỏa lifesteal-res nirvana berserker) — config flags.
    # L1 lifts the Hỏa res soft-cap to 0.90 (``hoa_max_resist_bonus``, existing
    # lane) and spills the overcap into ``element_dmg_bonus.hoa`` (overcap block
    # in compute_combat_stats reads ``hoa_overcap_to_dmg``); immune to Hỏa-DoT
    # debuffs (interceptor reads ``immune_hoa_skill_debuffs``). L6 one-use revive
    # (revives.py dedicated hook reads ``niet_ban_revive_*``); L9 arms the
    # post-revive berserk (``niet_ban_post_revive_boost``). Nghiệp Hỏa tiers
    # accumulate via the niet_ban periodic hook (gate ``nb_nghiep_accumulate``,
    # revive bump ``nb_nghiep_revive_pct_per_tier``). Runtime counters
    # (nb_nghiep_tier / nb_nghiep_progress / niet_ban_revive_used) are
    # Combatant-only and excluded.
    ("hoa_overcap_to_dmg",               "hoa_overcap_to_dmg",               bool),
    ("immune_hoa_skill_debuffs",         "immune_hoa_skill_debuffs",         bool),
    ("nb_nghiep_accumulate",             "nb_nghiep_accumulate",             bool),
    ("nb_nghiep_revive_pct_per_tier",    "nb_nghiep_revive_pct_per_tier",    float),
    ("niet_ban_revive_enabled",          "niet_ban_revive_enabled",          bool),
    ("niet_ban_revive_pct",              "niet_ban_revive_pct",              float),
    ("niet_ban_revive_clear_debuffs",    "niet_ban_revive_clear_debuffs",    bool),
    ("niet_ban_post_revive_boost",       "niet_ban_post_revive_boost",       bool),
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
    # Thiên Thủy Thánh Thể (Thủy holy-spring sustain tank)
    tt_dmg_convert_heal_pct: float = 0.0
    tt_reflect_remainder_pct: float = 0.0
    tt_heal_cleanse_chance: float = 0.0
    tt_tinh_hoa_cap: int = 0
    tt_tinh_hoa_per_stack_cleanse: float = 0.0
    tt_tinh_hoa_mp_on_heal_pct: float = 0.0
    tt_abyss_threshold: int = 0
    tt_abyss_reduce_pct: float = 0.0
    tt_abyss_reflect_pct: float = 0.0
    # Lưu Ly Thuẫn Thân Thể (universal shield-only aegis body)
    shield_only_body: bool = False
    shield_from_hp_max_pct: float = 0.0
    heal_to_shield_pct: float = 0.0
    aegis_reform_charges: int = 0
    aegis_reform_shield_pct: float = 0.0
    # Liệt Diễm Phần Thiên Thể (Hỏa escalating fire nuker)
    lietdiem_burn_per_turn: int = 0
    lietdiem_burn_cap: int = 0
    lietdiem_van_hoa_absorb: bool = False
    lietdiem_van_hoa_cap: int = 0
    lietdiem_avatar_enabled: bool = False
    lietdiem_avatar_interval: int = 0
    lietdiem_avatar_duration: int = 0
    # Niết Bàn Bất Diệt Thể (Hỏa lifesteal-res nirvana berserker)
    hoa_overcap_to_dmg: bool = False
    immune_hoa_skill_debuffs: bool = False
    nb_nghiep_accumulate: bool = False
    nb_nghiep_revive_pct_per_tier: float = 0.0
    niet_ban_revive_enabled: bool = False
    niet_ban_revive_pct: float = 0.0
    niet_ban_revive_clear_debuffs: bool = False
    niet_ban_post_revive_boost: bool = False
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
