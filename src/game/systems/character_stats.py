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


def active_formation_gem_keys(player) -> list[str]:
    """Extract the gem_keys list of the player's active formation.

    Accepts any object exposing ``active_formation`` and an iterable ``formations``
    whose items have ``formation_key`` + ``gem_slots``. Returns an empty list when
    no formation is active or no gems are inlaid.
    """
    active = getattr(player, "active_formation", None)
    if not active:
        return []
    for f in getattr(player, "formations", None) or []:
        if getattr(f, "formation_key", None) == active:
            return list((f.gem_slots or {}).values())
    return []


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
    final_dmg_bonus: float      # includes realm_power_bonus
    final_dmg_reduce: float
    hp_regen_pct: float
    hp_regen_flat: int         # flat HP per turn (stacks with pct regen)
    mp_regen_pct: float
    mp_regen_flat: int         # flat MP per turn (stacks with pct regen)
    heal_pct: float
    cooldown_reduce: float
    burn_on_hit_pct: float
    slow_on_hit_pct: float
    paralysis_on_crit: bool
    freeze_on_skill: bool
    poison_immunity: bool
    debuff_immune_pct: float
    # ── Fire-DoT build ────────────────────────────────────────────────────
    burn_stack_cap: int = 5
    burn_per_stack_pct: float = 0.012
    bonus_dmg_vs_burn: float = 0.0
    fire_res_shred: float = 0.0
    dot_can_crit: bool = False
    # ── Kim (bleed) build ─────────────────────────────────────────────────
    bleed_stack_cap: int = 5
    bleed_per_stack_pct: float = 0.010
    bleed_on_hit_pct: float = 0.0
    bleed_heal_reduce: float = 0.0
    crit_rating_vs_bleed: int = 0
    crit_dmg_vs_bleed: int = 0
    true_dmg_pct: float = 0.0
    # ── Moc (wood/poison-leech/heal) build ────────────────────────────────
    dot_leech_pct: float = 0.0
    moc_res_shred: float = 0.0
    damage_from_heal_pct: float = 0.0
    damage_bonus_from_hp_pct: float = 0.0
    # ── Thủy (water/mana/mirror) build ────────────────────────────────────
    reflect_pct: float = 0.0
    reflect_applies_effects: bool = False
    damage_bonus_from_mp_pct: float = 0.0
    mp_leech_pct: float = 0.0
    mana_stack_cap: int = 10
    mana_stack_per_attack: int = 0
    mana_stack_dmg_bonus: float = 0.0
    thuy_res_shred: float = 0.0
    # ── Thổ (earth/shield/thorn) build ────────────────────────────────────
    shield_regen_pct: float = 0.0
    shield_regen_flat: int = 0
    shield_cap_pct: float = 0.30
    damage_bonus_from_shield_pct: float = 0.0
    thorn_pct: float = 0.0
    thorn_from_shield: bool = False
    stun_on_hit_pct: float = 0.0
    mp_reserved: int = 0          # MP locked by active formation
    mp_reserve_pct: float = 0.0   # fraction of raw mp_max that's reserved
    resistances: dict[str, float] = field(default_factory=dict)


def compute_combat_stats(
    char: Character,
    gem_count: int = 0,
    equip_stats: dict | None = None,
    gem_keys: list[str] | None = None,
) -> CombatStats:
    """Compute all derived combat stats for a player character.

    Applies (in order): formation bonuses → constitution bonuses →
    linh_can bonuses → realm_power_bonus → equipment bonuses →
    formation MP reservation (locks part of mp_max).

    Args:
        char:        Character dataclass (from DB or model layer).
        gem_count:   Number of gems inlaid in the active formation (used if
                     ``gem_keys`` is not supplied).
        equip_stats: Pre-computed equipment stat totals from
                     ``compute_equipment_stats(equipped_instances)``.
                     Pass None (or {}) when equipment should be ignored.
        gem_keys:    Explicit list of gem item_keys (e.g. ``["GemKim_2", ...]``).
                     When provided, per-gem elemental bonuses are added on top
                     of the formation's threshold bonuses.

    Returns:
        CombatStats with every field ready for use in Combatant construction
        or status-embed display. ``mp_max`` is already reduced by reservation;
        ``mp_reserved`` carries the locked amount for UI.
    """
    from src.game.systems.cultivation import (
        compute_hp_max, compute_mp_max,
        compute_atk, compute_matk, compute_def_stat,
        compute_formation_bonuses, compute_constitution_bonuses, merge_bonuses,
    )
    from src.game.constants.linh_can import compute_linh_can_bonuses

    # ── Bonus merge ──────────────────────────────────────────────────────────
    # Trận Đạo cultivation progress scales formation bonuses: late-game
    # formation cultivators get meaningfully stronger formation effects.
    formation_stages = char.formation_realm * 9 + char.formation_level
    form_bonuses  = compute_formation_bonuses(
        char.active_formation,
        gem_count=gem_count,
        gem_keys=gem_keys,
        formation_stages=formation_stages,
    )
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
    hp_regen_flat    = int(bonuses.get("hp_regen_flat", 0))
    mp_regen_pct     = BASE_MP_REGEN_PCT + bonuses.get("mp_regen_pct", 0.0)
    mp_regen_flat    = int(bonuses.get("mp_regen_flat", 0))
    heal_pct         = bonuses.get("heal_pct", 0.0)
    cooldown_reduce  = char.stats.cooldown_reduce + bonuses.get("cooldown_reduce", 0.0)

    # On-hit / on-crit procs from formation thresholds
    burn_on_hit_pct   = bonuses.get("burn_on_hit_pct", 0.0)
    burn_stack_cap_bonus = int(bonuses.get("burn_stack_cap_bonus", 0))
    burn_per_stack_pct_bonus = float(bonuses.get("burn_per_stack_pct_bonus", 0.0))
    bonus_dmg_vs_burn = float(bonuses.get("bonus_dmg_vs_burn", 0.0))
    fire_res_shred   = float(bonuses.get("fire_res_shred", 0.0))
    dot_can_crit      = bool(bonuses.get("dot_can_crit", False))
    # Kim-build fields
    bleed_on_hit_pct        = float(bonuses.get("bleed_on_hit_pct", 0.0))
    bleed_stack_cap_bonus   = int(bonuses.get("bleed_stack_cap_bonus", 0))
    bleed_per_stack_pct_bonus = float(bonuses.get("bleed_per_stack_pct_bonus", 0.0))
    bleed_heal_reduce       = float(bonuses.get("bleed_heal_reduce", 0.0))
    crit_rating_vs_bleed    = int(bonuses.get("crit_rating_vs_bleed", 0))
    crit_dmg_vs_bleed       = int(bonuses.get("crit_dmg_vs_bleed", 0))
    true_dmg_pct            = float(bonuses.get("true_dmg_pct", 0.0))
    # Moc-build fields
    dot_leech_pct            = float(bonuses.get("dot_leech_pct", 0.0))
    moc_res_shred            = float(bonuses.get("moc_res_shred", 0.0))
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
    thuy_res_shred           = float(bonuses.get("thuy_res_shred", 0.0))
    # Thổ-build fields
    shield_regen_pct             = float(bonuses.get("shield_regen_pct", 0.0))
    shield_regen_flat            = int(bonuses.get("shield_regen_flat", 0))
    shield_cap_pct_bonus         = float(bonuses.get("shield_cap_pct_bonus", 0.0))
    damage_bonus_from_shield_pct = float(bonuses.get("damage_bonus_from_shield_pct", 0.0))
    thorn_pct                    = float(bonuses.get("thorn_pct", 0.0))
    thorn_from_shield            = bool(bonuses.get("thorn_from_shield", False))
    stun_on_hit_pct              = float(bonuses.get("stun_on_hit_pct", 0.0))
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
        hp_regen_pct  += equip_stats.get("hp_regen_pct", 0.0)
        hp_regen_flat += int(equip_stats.get("hp_regen_flat", 0))
        mp_regen_pct  += equip_stats.get("mp_regen_pct", 0.0)
        mp_regen_flat += int(equip_stats.get("mp_regen_flat", 0))
        hp_max += int(equip_stats.get("hp_max", 0))
        mp_max += int(equip_stats.get("mp_max", 0))

        eq_res_all = equip_stats.get("res_all", 0.0)
        if eq_res_all:
            for elem in resistances:
                resistances[elem] = min(0.75, resistances[elem] + eq_res_all)

        # Passive bonuses from unique items (on-hit procs, immunities, etc.)
        burn_on_hit_pct   += equip_stats.get("burn_on_hit_pct", 0.0)
        burn_stack_cap_bonus    += int(equip_stats.get("burn_stack_cap_bonus", 0))
        burn_per_stack_pct_bonus+= float(equip_stats.get("burn_per_stack_pct_bonus", 0.0))
        bonus_dmg_vs_burn += float(equip_stats.get("bonus_dmg_vs_burn", 0.0))
        fire_res_shred    += float(equip_stats.get("fire_res_shred", 0.0))
        dot_can_crit       = dot_can_crit or bool(equip_stats.get("dot_can_crit", False))
        # Kim-build fields
        bleed_on_hit_pct        += float(equip_stats.get("bleed_on_hit_pct", 0.0))
        bleed_stack_cap_bonus   += int(equip_stats.get("bleed_stack_cap_bonus", 0))
        bleed_per_stack_pct_bonus += float(equip_stats.get("bleed_per_stack_pct_bonus", 0.0))
        bleed_heal_reduce       += float(equip_stats.get("bleed_heal_reduce", 0.0))
        crit_rating_vs_bleed    += int(equip_stats.get("crit_rating_vs_bleed", 0))
        crit_dmg_vs_bleed       += int(equip_stats.get("crit_dmg_vs_bleed", 0))
        true_dmg_pct            += float(equip_stats.get("true_dmg_pct", 0.0))
        # Moc-build fields
        dot_leech_pct            += float(equip_stats.get("dot_leech_pct", 0.0))
        moc_res_shred            += float(equip_stats.get("moc_res_shred", 0.0))
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
        thuy_res_shred           += float(equip_stats.get("thuy_res_shred", 0.0))
        # Thổ-build fields
        shield_regen_pct             += float(equip_stats.get("shield_regen_pct", 0.0))
        shield_regen_flat            += int(equip_stats.get("shield_regen_flat", 0))
        shield_cap_pct_bonus         += float(equip_stats.get("shield_cap_pct_bonus", 0.0))
        damage_bonus_from_shield_pct += float(equip_stats.get("damage_bonus_from_shield_pct", 0.0))
        thorn_pct                    += float(equip_stats.get("thorn_pct", 0.0))
        thorn_from_shield             = thorn_from_shield or bool(equip_stats.get("thorn_from_shield", False))
        stun_on_hit_pct              += float(equip_stats.get("stun_on_hit_pct", 0.0))
        slow_on_hit_pct   += equip_stats.get("slow_on_hit_pct", 0.0)
        paralysis_on_crit  = paralysis_on_crit or bool(equip_stats.get("paralysis_on_crit", False))
        freeze_on_skill    = freeze_on_skill   or bool(equip_stats.get("freeze_on_skill", False))
        poison_immunity    = poison_immunity   or bool(equip_stats.get("poison_immunity", False))
        debuff_immune_pct += equip_stats.get("debuff_immune_pct", 0.0)
        heal_pct          += equip_stats.get("heal_pct", 0.0)
        cooldown_reduce   += equip_stats.get("cooldown_reduce", 0.0)

    # ── Formation MP reservation (applied last, after all bonuses) ────────────
    reserve_pct = max(0.0, float(form_bonuses.get("_mp_reserve_pct", 0.0)))
    mp_reserved = int(mp_max * reserve_pct)
    mp_max = max(0, mp_max - mp_reserved)

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
        hp_regen_flat=hp_regen_flat,
        mp_regen_pct=mp_regen_pct,
        mp_regen_flat=mp_regen_flat,
        heal_pct=heal_pct,
        cooldown_reduce=cooldown_reduce,
        burn_on_hit_pct=burn_on_hit_pct,
        slow_on_hit_pct=slow_on_hit_pct,
        paralysis_on_crit=paralysis_on_crit,
        freeze_on_skill=freeze_on_skill,
        poison_immunity=poison_immunity,
        debuff_immune_pct=debuff_immune_pct,
        burn_stack_cap=5 + burn_stack_cap_bonus,
        burn_per_stack_pct=0.012 + burn_per_stack_pct_bonus,
        bonus_dmg_vs_burn=bonus_dmg_vs_burn,
        fire_res_shred=fire_res_shred,
        dot_can_crit=dot_can_crit,
        bleed_stack_cap=5 + bleed_stack_cap_bonus,
        bleed_per_stack_pct=0.010 + bleed_per_stack_pct_bonus,
        bleed_on_hit_pct=bleed_on_hit_pct,
        bleed_heal_reduce=bleed_heal_reduce,
        crit_rating_vs_bleed=crit_rating_vs_bleed,
        crit_dmg_vs_bleed=crit_dmg_vs_bleed,
        true_dmg_pct=true_dmg_pct,
        dot_leech_pct=dot_leech_pct,
        moc_res_shred=moc_res_shred,
        damage_from_heal_pct=damage_from_heal_pct,
        damage_bonus_from_hp_pct=damage_bonus_from_hp_pct,
        reflect_pct=reflect_pct,
        reflect_applies_effects=reflect_applies_effects,
        damage_bonus_from_mp_pct=damage_bonus_from_mp_pct,
        mp_leech_pct=mp_leech_pct,
        mana_stack_cap=10 + mana_stack_cap_bonus,
        mana_stack_per_attack=mana_stack_per_attack,
        mana_stack_dmg_bonus=mana_stack_dmg_bonus,
        thuy_res_shred=thuy_res_shred,
        shield_regen_pct=shield_regen_pct,
        shield_regen_flat=shield_regen_flat,
        shield_cap_pct=0.30 + shield_cap_pct_bonus,
        damage_bonus_from_shield_pct=damage_bonus_from_shield_pct,
        thorn_pct=thorn_pct,
        thorn_from_shield=thorn_from_shield,
        stun_on_hit_pct=stun_on_hit_pct,
        mp_reserved=mp_reserved,
        mp_reserve_pct=reserve_pct,
        resistances=resistances,
    )
