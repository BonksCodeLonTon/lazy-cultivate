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
    DEFAULT_BURN_STACK_CAP,
    DEFAULT_BURN_PER_STACK_PCT,
    DEFAULT_BLEED_STACK_CAP,
    DEFAULT_BLEED_PER_STACK_PCT,
    DEFAULT_SHOCK_STACK_CAP,
    DEFAULT_SHOCK_PER_STACK_PCT,
    DEFAULT_MANA_STACK_CAP,
    DEFAULT_SHIELD_CAP_PCT,
)
from src.game.engine import linh_can_effects as lc_effects


def active_formation_gem_keys(player) -> list[str]:
    """Flattened gem_keys across EVERY active formation slot.

    ``player.active_formation`` holds a comma-separated list of formation keys
    (one per active slot); this returns the concatenation of each active
    formation's inlaid gems. Preserves the legacy single-slot return shape
    when the player has only one formation equipped.
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
    burn_stack_cap: int = DEFAULT_BURN_STACK_CAP
    burn_per_stack_pct: float = DEFAULT_BURN_PER_STACK_PCT
    bonus_dmg_vs_burn: float = 0.0
    fire_res_shred: float = 0.0
    dot_can_crit: bool = False
    # ── Kim (bleed) build ─────────────────────────────────────────────────
    bleed_stack_cap: int = DEFAULT_BLEED_STACK_CAP
    bleed_per_stack_pct: float = DEFAULT_BLEED_PER_STACK_PCT
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
    mana_stack_cap: int = DEFAULT_MANA_STACK_CAP
    mana_stack_per_attack: int = 0
    mana_stack_dmg_bonus: float = 0.0
    thuy_res_shred: float = 0.0
    # ── Thổ (earth/shield/thorn) build ────────────────────────────────────
    shield_regen_pct: float = 0.0
    shield_regen_flat: int = 0
    shield_cap_pct: float = DEFAULT_SHIELD_CAP_PCT
    damage_bonus_from_shield_pct: float = 0.0
    thorn_pct: float = 0.0
    thorn_from_shield: bool = False
    stun_on_hit_pct: float = 0.0
    # ── Lôi (lightning/shock/speed) build ─────────────────────────────────
    shock_stack_cap: int = DEFAULT_SHOCK_STACK_CAP
    shock_per_stack_pct: float = DEFAULT_SHOCK_PER_STACK_PCT
    shock_on_hit_pct: float = 0.0
    loi_res_shred: float = 0.0
    turn_steal_pct: float = 0.0
    # ── Phong (wind/evasion/mark) build ───────────────────────────────────
    mark_on_hit_pct: float = 0.0
    damage_bonus_from_evasion_pct: float = 0.0
    crit_rating_vs_marked: int = 0
    crit_dmg_vs_marked: int = 0
    phong_res_shred: float = 0.0
    # ── Quang (light/silence/anti-heal) build ─────────────────────────────
    silence_on_crit_pct: float = 0.0
    heal_reduce_on_hit_pct: float = 0.0
    cleanse_on_turn_pct: float = 0.0
    quang_res_shred: float = 0.0
    barrier_on_cleanse: bool = False
    # ── Âm (shadow/soul-devour) build ─────────────────────────────────────
    soul_drain_on_hit_pct: float = 0.0
    stat_steal_on_hit_pct: float = 0.0
    am_res_shred: float = 0.0
    crit_rating_vs_drained: int = 0
    # ── Mộc + Quang shared: heals may crit (×1.5) ─────────────────────────
    heal_can_crit: bool = False
    # ── DoT damage amplifiers (cross-build) ───────────────────────────────
    dot_dmg_bonus: float = 0.0
    burn_dmg_bonus: float = 0.0
    bleed_dmg_bonus: float = 0.0
    poison_dmg_bonus: float = 0.0
    # Rare late-game flag: reverts DoTs to the legacy %HP model.
    dot_scales_hp_pct: bool = False
    mp_reserved: int = 0          # MP locked by active formation
    mp_reserve_pct: float = 0.0   # fraction of raw mp_max that's reserved
    resistances: dict[str, float] = field(default_factory=dict)


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
    active_formations = get_active_formations(char.active_formation)

    # Build a per-formation gem map. Callers that only have a flat gem list
    # (legacy single-formation flow) still work because a single active
    # formation always owns all the gems.
    if gem_keys_by_formation is None:
        if len(active_formations) == 1 and gem_keys:
            gem_keys_by_formation = {active_formations[0]: list(gem_keys)}
        else:
            gem_keys_by_formation = {}

    form_bonuses = compute_formations_bonuses(
        active_formations,
        gem_keys_by_formation=gem_keys_by_formation,
        formation_stages=formation_stages,
    )
    const_bonuses = compute_constitution_bonuses(char.constitution_type)
    lc_bonuses    = compute_linh_can_bonuses(char.linh_can)

    # Mộc Linh Căn passive: Hồi Xuân — always heals a flat % HP per turn in combat
    moc_regen = lc_effects.get_regen_bonus(char.linh_can)
    if moc_regen:
        lc_bonuses["hp_regen_pct"] = lc_bonuses.get("hp_regen_pct", 0.0) + moc_regen

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

    # Equipment can contribute flat spd_bonus too (e.g. boot affixes).
    _equip_spd_bonus = int((equip_stats or {}).get("spd_bonus", 0))
    spd_base  = char.stats.spd + bonuses.get("spd_bonus", 0) + _equip_spd_bonus
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
    # Lôi-build fields
    shock_stack_cap_bonus        = int(bonuses.get("shock_stack_cap_bonus", 0))
    shock_per_stack_pct_bonus    = float(bonuses.get("shock_per_stack_pct_bonus", 0.0))
    shock_on_hit_pct             = float(bonuses.get("shock_on_hit_pct", 0.0))
    loi_res_shred                = float(bonuses.get("loi_res_shred", 0.0))
    turn_steal_pct               = float(bonuses.get("turn_steal_pct", 0.0))
    # Phong-build fields
    mark_on_hit_pct              = float(bonuses.get("mark_on_hit_pct", 0.0))
    damage_bonus_from_evasion_pct= float(bonuses.get("damage_bonus_from_evasion_pct", 0.0))
    crit_rating_vs_marked        = int(bonuses.get("crit_rating_vs_marked", 0))
    crit_dmg_vs_marked           = int(bonuses.get("crit_dmg_vs_marked", 0))
    phong_res_shred              = float(bonuses.get("phong_res_shred", 0.0))
    # Quang-build fields
    silence_on_crit_pct          = float(bonuses.get("silence_on_crit_pct", 0.0))
    heal_reduce_on_hit_pct       = float(bonuses.get("heal_reduce_on_hit_pct", 0.0))
    cleanse_on_turn_pct          = float(bonuses.get("cleanse_on_turn_pct", 0.0))
    quang_res_shred              = float(bonuses.get("quang_res_shred", 0.0))
    barrier_on_cleanse           = bool(bonuses.get("barrier_on_cleanse", False))
    heal_can_crit                = bool(bonuses.get("heal_can_crit", False))
    # Âm-build fields
    soul_drain_on_hit_pct        = float(bonuses.get("soul_drain_on_hit_pct", 0.0))
    stat_steal_on_hit_pct        = float(bonuses.get("stat_steal_on_hit_pct", 0.0))
    am_res_shred                 = float(bonuses.get("am_res_shred", 0.0))
    crit_rating_vs_drained       = int(bonuses.get("crit_rating_vs_drained", 0))
    # DoT-amplifier fields (cross-build)
    dot_dmg_bonus                = float(bonuses.get("dot_dmg_bonus", 0.0))
    burn_dmg_bonus               = float(bonuses.get("burn_dmg_bonus", 0.0))
    bleed_dmg_bonus              = float(bonuses.get("bleed_dmg_bonus", 0.0))
    poison_dmg_bonus             = float(bonuses.get("poison_dmg_bonus", 0.0))
    dot_scales_hp_pct            = bool(bonuses.get("dot_scales_hp_pct", False))
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
        # Lôi-build fields
        shock_stack_cap_bonus        += int(equip_stats.get("shock_stack_cap_bonus", 0))
        shock_per_stack_pct_bonus    += float(equip_stats.get("shock_per_stack_pct_bonus", 0.0))
        shock_on_hit_pct             += float(equip_stats.get("shock_on_hit_pct", 0.0))
        loi_res_shred                += float(equip_stats.get("loi_res_shred", 0.0))
        turn_steal_pct               += float(equip_stats.get("turn_steal_pct", 0.0))
        # Phong-build fields
        mark_on_hit_pct              += float(equip_stats.get("mark_on_hit_pct", 0.0))
        damage_bonus_from_evasion_pct+= float(equip_stats.get("damage_bonus_from_evasion_pct", 0.0))
        crit_rating_vs_marked        += int(equip_stats.get("crit_rating_vs_marked", 0))
        crit_dmg_vs_marked           += int(equip_stats.get("crit_dmg_vs_marked", 0))
        phong_res_shred              += float(equip_stats.get("phong_res_shred", 0.0))
        # Quang-build fields
        silence_on_crit_pct          += float(equip_stats.get("silence_on_crit_pct", 0.0))
        heal_reduce_on_hit_pct       += float(equip_stats.get("heal_reduce_on_hit_pct", 0.0))
        cleanse_on_turn_pct          += float(equip_stats.get("cleanse_on_turn_pct", 0.0))
        quang_res_shred              += float(equip_stats.get("quang_res_shred", 0.0))
        barrier_on_cleanse            = barrier_on_cleanse or bool(equip_stats.get("barrier_on_cleanse", False))
        heal_can_crit                 = heal_can_crit or bool(equip_stats.get("heal_can_crit", False))
        # Âm-build fields
        soul_drain_on_hit_pct        += float(equip_stats.get("soul_drain_on_hit_pct", 0.0))
        stat_steal_on_hit_pct        += float(equip_stats.get("stat_steal_on_hit_pct", 0.0))
        am_res_shred                 += float(equip_stats.get("am_res_shred", 0.0))
        crit_rating_vs_drained       += int(equip_stats.get("crit_rating_vs_drained", 0))
        # DoT-amplifier fields
        dot_dmg_bonus                += float(equip_stats.get("dot_dmg_bonus", 0.0))
        burn_dmg_bonus               += float(equip_stats.get("burn_dmg_bonus", 0.0))
        bleed_dmg_bonus              += float(equip_stats.get("bleed_dmg_bonus", 0.0))
        poison_dmg_bonus             += float(equip_stats.get("poison_dmg_bonus", 0.0))
        dot_scales_hp_pct             = dot_scales_hp_pct or bool(equip_stats.get("dot_scales_hp_pct", False))
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
        burn_stack_cap=DEFAULT_BURN_STACK_CAP + burn_stack_cap_bonus,
        burn_per_stack_pct=DEFAULT_BURN_PER_STACK_PCT + burn_per_stack_pct_bonus,
        bonus_dmg_vs_burn=bonus_dmg_vs_burn,
        fire_res_shred=fire_res_shred,
        dot_can_crit=dot_can_crit,
        bleed_stack_cap=DEFAULT_BLEED_STACK_CAP + bleed_stack_cap_bonus,
        bleed_per_stack_pct=DEFAULT_BLEED_PER_STACK_PCT + bleed_per_stack_pct_bonus,
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
        mana_stack_cap=DEFAULT_MANA_STACK_CAP + mana_stack_cap_bonus,
        mana_stack_per_attack=mana_stack_per_attack,
        mana_stack_dmg_bonus=mana_stack_dmg_bonus,
        thuy_res_shred=thuy_res_shred,
        shield_regen_pct=shield_regen_pct,
        shield_regen_flat=shield_regen_flat,
        shield_cap_pct=DEFAULT_SHIELD_CAP_PCT + shield_cap_pct_bonus,
        damage_bonus_from_shield_pct=damage_bonus_from_shield_pct,
        thorn_pct=thorn_pct,
        thorn_from_shield=thorn_from_shield,
        stun_on_hit_pct=stun_on_hit_pct,
        shock_stack_cap=DEFAULT_SHOCK_STACK_CAP + shock_stack_cap_bonus,
        shock_per_stack_pct=DEFAULT_SHOCK_PER_STACK_PCT + shock_per_stack_pct_bonus,
        shock_on_hit_pct=shock_on_hit_pct,
        loi_res_shred=loi_res_shred,
        turn_steal_pct=turn_steal_pct,
        mark_on_hit_pct=mark_on_hit_pct,
        damage_bonus_from_evasion_pct=damage_bonus_from_evasion_pct,
        crit_rating_vs_marked=crit_rating_vs_marked,
        crit_dmg_vs_marked=crit_dmg_vs_marked,
        phong_res_shred=phong_res_shred,
        silence_on_crit_pct=silence_on_crit_pct,
        heal_reduce_on_hit_pct=heal_reduce_on_hit_pct,
        cleanse_on_turn_pct=cleanse_on_turn_pct,
        quang_res_shred=quang_res_shred,
        barrier_on_cleanse=barrier_on_cleanse,
        heal_can_crit=heal_can_crit,
        soul_drain_on_hit_pct=soul_drain_on_hit_pct,
        stat_steal_on_hit_pct=stat_steal_on_hit_pct,
        am_res_shred=am_res_shred,
        crit_rating_vs_drained=crit_rating_vs_drained,
        dot_dmg_bonus=dot_dmg_bonus,
        burn_dmg_bonus=burn_dmg_bonus,
        bleed_dmg_bonus=bleed_dmg_bonus,
        poison_dmg_bonus=poison_dmg_bonus,
        dot_scales_hp_pct=dot_scales_hp_pct,
        mp_reserved=mp_reserved,
        mp_reserve_pct=reserve_pct,
        resistances=resistances,
    )
