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
    MAX_FINAL_DMG_REDUCE,
)


@dataclass
class CombatStats:
    """All derived stats for one character — cultivation + formation + constitution + linh_can + equipment."""
    hp_max: int
    mp_max: int
    atk: int
    matk: int
    def_stat: int
    spd: int
    crit_rating: int
    crit_dmg_rating: int
    evasion_rating: int
    crit_res_rating: int
    final_dmg_bonus: float      # includes realm_power_bonus
    final_dmg_reduce: float
    hp_regen_pct: float
    mp_regen_pct: float
    heal_pct: float
    cooldown_reduce: float
    burn_on_hit_pct: float
    slow_on_hit_pct: float
    paralysis_on_crit: bool
    freeze_on_skill: bool
    poison_immunity: bool
    debuff_immune_pct: float
    resistances: dict[str, float] = field(default_factory=dict)


def compute_combat_stats(
    char: Character,
    gem_count: int = 0,
    equip_stats: dict | None = None,
) -> CombatStats:
    """Compute all derived combat stats for a player character.

    Applies (in order): formation bonuses → constitution bonuses →
    linh_can bonuses → realm_power_bonus → equipment bonuses.

    Args:
        char:        Character dataclass (from DB or model layer).
        gem_count:   Number of gems inlaid in the active formation.
        equip_stats: Pre-computed equipment stat totals from
                     ``compute_equipment_stats(equipped_instances)``.
                     Pass None (or {}) when equipment should be ignored.

    Returns:
        CombatStats with every field ready for use in Combatant construction
        or status-embed display.
    """
    from src.game.systems.cultivation import (
        compute_hp_max, compute_mp_max,
        compute_atk, compute_matk, compute_def_stat,
        compute_formation_bonuses, compute_constitution_bonuses, merge_bonuses,
    )
    from src.game.constants.linh_can import compute_linh_can_bonuses

    # ── Bonus merge ──────────────────────────────────────────────────────────
    form_bonuses  = compute_formation_bonuses(char.active_formation, gem_count)
    const_bonuses = compute_constitution_bonuses(char.constitution_type)
    lc_bonuses    = compute_linh_can_bonuses(char.linh_can)

    # Mộc Linh Căn passive: Hồi Xuân — always heals 4% HP per turn in combat
    if "moc" in char.linh_can:
        lc_bonuses["hp_regen_pct"] = lc_bonuses.get("hp_regen_pct", 0.0) + 0.04

    bonuses = merge_bonuses(form_bonuses, const_bonuses, lc_bonuses)

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

    spd_base  = char.stats.spd + bonuses.get("spd_bonus", 0)
    spd_final = round(spd_base * (1.0 + bonuses.get("spd_pct", 0.0)))

    # ── Combat ratings ────────────────────────────────────────────────────────
    crit_rating     = char.stats.crit_rating     + bonuses.get("crit_rating", 0)
    crit_dmg_rating = char.stats.crit_dmg_rating + bonuses.get("crit_dmg_rating", 0)
    evasion_rating  = char.stats.evasion_rating  + bonuses.get("evasion_rating", 0)
    crit_res_rating = char.stats.crit_res_rating + bonuses.get("crit_res_rating", 0)

    final_dmg_bonus  = char.stats.final_dmg_bonus + bonuses.get("final_dmg_bonus", 0.0) + realm_power_bonus
    final_dmg_reduce = bonuses.get("final_dmg_reduce", 0.0)
    hp_regen_pct     = bonuses.get("hp_regen_pct", 0.0)
    mp_regen_pct     = BASE_MP_REGEN_PCT + bonuses.get("mp_regen_pct", 0.0)
    heal_pct         = bonuses.get("heal_pct", 0.0)
    cooldown_reduce  = char.stats.cooldown_reduce + bonuses.get("cooldown_reduce", 0.0)

    # On-hit / on-crit procs from formation thresholds
    burn_on_hit_pct   = bonuses.get("burn_on_hit_pct", 0.0)
    slow_on_hit_pct   = bonuses.get("slow_on_hit_pct", 0.0)
    paralysis_on_crit = bonuses.get("paralysis_on_crit", False)
    freeze_on_skill   = bonuses.get("freeze_on_skill", False)
    poison_immunity   = bonuses.get("poison_immunity", False)
    debuff_immune_pct = bonuses.get("debuff_immune_pct", 0.0)

    # ── Resistances ───────────────────────────────────────────────────────────
    resistances: dict[str, float] = {
        "kim":   char.stats.res_kim,
        "moc":   char.stats.res_moc,
        "thuy":  char.stats.res_thuy,
        "hoa":   char.stats.res_hoa,
        "tho":   char.stats.res_tho,
        "loi":   char.stats.res_loi,
        "phong": char.stats.res_phong,
        "quang": char.stats.res_quang,
        "am":    char.stats.res_am,
    }

    res_all = bonuses.get("res_all", 0.0)
    if res_all:
        for elem in resistances:
            resistances[elem] = min(0.75, max(0.0, resistances[elem] + res_all))

    form_elem = form_bonuses.get("_formation_element")
    res_elem  = form_bonuses.get("res_element", 0.0)
    if form_elem and res_elem:
        resistances[form_elem] = min(0.75, max(0.0, resistances.get(form_elem, 0.0) + res_elem))

    # ── Equipment bonuses (applied last) ──────────────────────────────────────
    if equip_stats:
        atk             += int(equip_stats.get("atk", 0))
        matk            += int(equip_stats.get("matk", 0))
        def_stat        += int(equip_stats.get("def_stat", 0))
        crit_rating     += int(equip_stats.get("crit_rating", 0))
        crit_dmg_rating += int(equip_stats.get("crit_dmg_rating", 0))
        evasion_rating  += int(equip_stats.get("evasion_rating", 0))
        crit_res_rating += int(equip_stats.get("crit_res_rating", 0))
        final_dmg_bonus  += equip_stats.get("final_dmg_bonus", 0.0)
        final_dmg_reduce  = min(
            MAX_FINAL_DMG_REDUCE,
            final_dmg_reduce + equip_stats.get("final_dmg_reduce", 0.0),
        )
        hp_regen_pct += equip_stats.get("hp_regen_pct", 0.0)
        hp_max += int(equip_stats.get("hp_max", 0))
        mp_max += int(equip_stats.get("mp_max", 0))

        eq_res_all = equip_stats.get("res_all", 0.0)
        if eq_res_all:
            for elem in resistances:
                resistances[elem] = min(0.75, resistances[elem] + eq_res_all)

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
        final_dmg_bonus=final_dmg_bonus,
        final_dmg_reduce=final_dmg_reduce,
        hp_regen_pct=hp_regen_pct,
        mp_regen_pct=mp_regen_pct,
        heal_pct=heal_pct,
        cooldown_reduce=cooldown_reduce,
        burn_on_hit_pct=burn_on_hit_pct,
        slow_on_hit_pct=slow_on_hit_pct,
        paralysis_on_crit=paralysis_on_crit,
        freeze_on_skill=freeze_on_skill,
        poison_immunity=poison_immunity,
        debuff_immune_pct=debuff_immune_pct,
        resistances=resistances,
    )
