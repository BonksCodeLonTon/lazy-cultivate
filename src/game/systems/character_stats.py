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
    MAX_ELEMENTAL_RES,
    DEFAULT_BURN_STACK_CAP,
    DEFAULT_BURN_PER_STACK_PCT,
    DEFAULT_BLEED_STACK_CAP,
    DEFAULT_BLEED_PER_STACK_PCT,
    DEFAULT_SHOCK_STACK_CAP,
    DEFAULT_SHOCK_PER_STACK_PCT,
    DEFAULT_MANA_STACK_CAP,
    DEFAULT_SHIELD_RECHARGE_DELAY,
)
from src.game.constants.elements import ALL_ELEMENTS, RESISTANCE_KEYS
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
    freeze_on_skill_chance: float
    poison_immunity: bool
    debuff_immune_pct: float
    # ── Fire-DoT build ────────────────────────────────────────────────────
    burn_stack_cap: int = DEFAULT_BURN_STACK_CAP
    burn_per_stack_pct: float = DEFAULT_BURN_PER_STACK_PCT
    bonus_dmg_vs_burn: float = 0.0
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
    shield_recharge_delay: int = DEFAULT_SHIELD_RECHARGE_DELAY
    damage_bonus_from_shield_pct: float = 0.0
    thorn_pct: float = 0.0
    thorn_from_shield: bool = False
    stun_on_hit_pct: float = 0.0
    # ── Lôi (lightning/shock/speed) build ─────────────────────────────────
    shock_stack_cap: int = DEFAULT_SHOCK_STACK_CAP
    shock_per_stack_pct: float = DEFAULT_SHOCK_PER_STACK_PCT
    shock_on_hit_pct: float = 0.0
    turn_steal_pct: float = 0.0
    # ── Phong (wind/evasion/mark) build ───────────────────────────────────
    mark_on_hit_pct: float = 0.0
    damage_bonus_from_evasion_pct: float = 0.0
    crit_rating_vs_marked: int = 0
    crit_dmg_vs_marked: int = 0
    # ── Quang (light/silence/anti-heal) build ─────────────────────────────
    silence_on_crit_pct: float = 0.0
    heal_reduce_on_hit_pct: float = 0.0
    cleanse_on_turn_pct: float = 0.0
    barrier_on_cleanse: bool = False
    # ── Âm (shadow/soul-devour) build ─────────────────────────────────────
    soul_drain_on_hit_pct: float = 0.0
    stat_steal_on_hit_pct: float = 0.0
    crit_rating_vs_drained: int = 0
    # ── Cross-element penetration — single dict source of truth ───────────
    # Passive attacker-side stat. Stacks additively with target debuffs
    # (DebuffXuyenThau<Elem>, DebuffXeRach via res_all).
    element_pen: dict[str, float] = field(default_factory=dict)
    # ── Mộc + Quang shared: heals may crit (×1.5) ─────────────────────────
    heal_can_crit: bool = False
    # ── DoT damage amplifiers (cross-build) ───────────────────────────────
    dot_dmg_bonus: float = 0.0
    burn_dmg_bonus: float = 0.0
    bleed_dmg_bonus: float = 0.0
    poison_dmg_bonus: float = 0.0
    # Rare late-game flag: reverts DoTs to the legacy %HP model.
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
    # Loot economy passives (constitutions / equipment) applied inside
    # CombatSession._roll_loot — additive on top of the session's baseline
    # loot_qty_multiplier (elite roll, dungeon grade) and loot_luck_pct.
    loot_qty_bonus: float = 0.0
    loot_luck_bonus: float = 0.0
    # Generic per-element bonus dicts (constitutions / equipment).
    damage_taken_convert_pct: dict[str, float] = field(default_factory=dict)
    element_dmg_bonus: dict[str, float] = field(default_factory=dict)
    mp_reserved: int = 0          # MP locked by active formation
    mp_reserve_pct: float = 0.0   # fraction of raw mp_max that's reserved
    resistances: dict[str, float] = field(default_factory=dict)


def compute_combat_stats(
    char: Character,
    gem_count: int = 0,
    equip_stats: dict | None = None,
    gem_keys: list[str] | None = None,
    gem_keys_by_formation: dict[str, list[str]] | None = None,
    learned_skill_keys: list[str] | None = None,
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
        compute_formation_skill_reserve_pct,
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
    # Per-element levels drive the linh_can stat scaling. Falls back to the
    # legacy list (treated as level 1 each) when ``linh_can_levels`` isn't set
    # — keeps NPC-style Characters that never went through the DB working.
    lc_levels = dict(getattr(char, "linh_can_levels", {}) or {})
    if not lc_levels:
        lc_levels = {elem: 1 for elem in char.linh_can}
    lc_bonuses    = compute_linh_can_bonuses(lc_levels)

    # Khí Tu archetype payoff — rewards breadth (many high-level Linh Căn)
    # in the same data-driven shape as Hỗn Độn's all_passives_multiplier.
    # Gated on is_khi_tu so a Thể-leaning player can't dip into the bonus
    # by maxing qi later. Compound-offensive stats are excluded for the
    # same reason they're excluded from Hỗn Độn — preventing one-shot
    # damage against world bosses.
    from src.game.systems.cultivation import is_khi_tu
    from src.game.constants.balance import LINH_CAN_BREADTH_MAX_MULT
    from src.game.constants.linh_can import linh_can_breadth_multiplier
    _BREADTH_EXCLUDED_STATS: frozenset[str] = frozenset({
        "final_dmg_bonus", "true_dmg_pct", "cooldown_reduce", "final_dmg_reduce",
    })
    if is_khi_tu(char.body_realm, char.qi_realm, char.formation_realm):
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
    dot_can_crit      = bool(bonuses.get("dot_can_crit", False))
    # Per-element penetration — single dict-of-dicts pulled from bonuses.
    # Constitutions / Linh Căn / formations all emit ``element_pen``;
    # equip-side merge happens further below.
    element_pen: dict[str, float] = {
        e: float(v) for e, v in (bonuses.get("element_pen") or {}).items()
    }
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
    # Negative bonus shortens the recharge pause; clamp at 0 (instant regen).
    shield_recharge_delay_bonus  = int(bonuses.get("shield_recharge_delay_bonus", 0))
    damage_bonus_from_shield_pct = float(bonuses.get("damage_bonus_from_shield_pct", 0.0))
    thorn_pct                    = float(bonuses.get("thorn_pct", 0.0))
    thorn_from_shield            = bool(bonuses.get("thorn_from_shield", False))
    stun_on_hit_pct              = float(bonuses.get("stun_on_hit_pct", 0.0))
    # Lôi-build fields
    shock_stack_cap_bonus        = int(bonuses.get("shock_stack_cap_bonus", 0))
    shock_per_stack_pct_bonus    = float(bonuses.get("shock_per_stack_pct_bonus", 0.0))
    shock_on_hit_pct             = float(bonuses.get("shock_on_hit_pct", 0.0))
    turn_steal_pct               = float(bonuses.get("turn_steal_pct", 0.0))
    # Phong-build fields
    mark_on_hit_pct              = float(bonuses.get("mark_on_hit_pct", 0.0))
    damage_bonus_from_evasion_pct= float(bonuses.get("damage_bonus_from_evasion_pct", 0.0))
    crit_rating_vs_marked        = int(bonuses.get("crit_rating_vs_marked", 0))
    crit_dmg_vs_marked           = int(bonuses.get("crit_dmg_vs_marked", 0))
    # Quang-build fields
    silence_on_crit_pct          = float(bonuses.get("silence_on_crit_pct", 0.0))
    heal_reduce_on_hit_pct       = float(bonuses.get("heal_reduce_on_hit_pct", 0.0))
    cleanse_on_turn_pct          = float(bonuses.get("cleanse_on_turn_pct", 0.0))
    barrier_on_cleanse           = bool(bonuses.get("barrier_on_cleanse", False))
    heal_can_crit                = bool(bonuses.get("heal_can_crit", False))
    # Âm-build fields
    soul_drain_on_hit_pct        = float(bonuses.get("soul_drain_on_hit_pct", 0.0))
    stat_steal_on_hit_pct        = float(bonuses.get("stat_steal_on_hit_pct", 0.0))
    crit_rating_vs_drained       = int(bonuses.get("crit_rating_vs_drained", 0))
    # DoT-amplifier fields (cross-build)
    dot_dmg_bonus                = float(bonuses.get("dot_dmg_bonus", 0.0))
    burn_dmg_bonus               = float(bonuses.get("burn_dmg_bonus", 0.0))
    bleed_dmg_bonus              = float(bonuses.get("bleed_dmg_bonus", 0.0))
    poison_dmg_bonus             = float(bonuses.get("poison_dmg_bonus", 0.0))
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
    loot_qty_bonus               = float(bonuses.get("loot_qty_bonus", 0.0))
    loot_luck_bonus              = float(bonuses.get("loot_luck_bonus", 0.0))
    damage_taken_convert_pct: dict[str, float] = dict(bonuses.get("damage_taken_convert_pct", {}) or {})
    element_dmg_bonus: dict[str, float] = dict(bonuses.get("element_dmg_bonus", {}) or {})
    slow_on_hit_pct   = bonuses.get("slow_on_hit_pct", 0.0)
    paralysis_on_crit = bonuses.get("paralysis_on_crit", False)
    freeze_on_skill   = bonuses.get("freeze_on_skill", False)
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
                resistances[elem] = min(MAX_ELEMENTAL_RES, resistances[elem] + eq_res_all)
        for elem in resistances:
            eq_bump = float(equip_stats.get(f"res_{elem}", 0.0))
            if eq_bump:
                resistances[elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances[elem] + eq_bump))

        # Passive bonuses from unique items (on-hit procs, immunities, etc.)
        burn_on_hit_pct   += equip_stats.get("burn_on_hit_pct", 0.0)
        burn_stack_cap_bonus    += int(equip_stats.get("burn_stack_cap_bonus", 0))
        burn_per_stack_pct_bonus+= float(equip_stats.get("burn_per_stack_pct_bonus", 0.0))
        bonus_dmg_vs_burn += float(equip_stats.get("bonus_dmg_vs_burn", 0.0))
        dot_can_crit       = dot_can_crit or bool(equip_stats.get("dot_can_crit", False))
        # Per-element penetration dict from equip — additive merge.
        for _e, _v in (equip_stats.get("element_pen") or {}).items():
            element_pen[_e] = element_pen.get(_e, 0.0) + float(_v)
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
        # Lôi-build fields
        shock_stack_cap_bonus        += int(equip_stats.get("shock_stack_cap_bonus", 0))
        shock_per_stack_pct_bonus    += float(equip_stats.get("shock_per_stack_pct_bonus", 0.0))
        shock_on_hit_pct             += float(equip_stats.get("shock_on_hit_pct", 0.0))
        turn_steal_pct               += float(equip_stats.get("turn_steal_pct", 0.0))
        # Phong-build fields
        mark_on_hit_pct              += float(equip_stats.get("mark_on_hit_pct", 0.0))
        damage_bonus_from_evasion_pct+= float(equip_stats.get("damage_bonus_from_evasion_pct", 0.0))
        crit_rating_vs_marked        += int(equip_stats.get("crit_rating_vs_marked", 0))
        crit_dmg_vs_marked           += int(equip_stats.get("crit_dmg_vs_marked", 0))
        # Quang-build fields
        silence_on_crit_pct          += float(equip_stats.get("silence_on_crit_pct", 0.0))
        heal_reduce_on_hit_pct       += float(equip_stats.get("heal_reduce_on_hit_pct", 0.0))
        cleanse_on_turn_pct          += float(equip_stats.get("cleanse_on_turn_pct", 0.0))
        barrier_on_cleanse            = barrier_on_cleanse or bool(equip_stats.get("barrier_on_cleanse", False))
        heal_can_crit                 = heal_can_crit or bool(equip_stats.get("heal_can_crit", False))
        # Âm-build fields
        soul_drain_on_hit_pct        += float(equip_stats.get("soul_drain_on_hit_pct", 0.0))
        stat_steal_on_hit_pct        += float(equip_stats.get("stat_steal_on_hit_pct", 0.0))
        crit_rating_vs_drained       += int(equip_stats.get("crit_rating_vs_drained", 0))
        # DoT-amplifier fields
        dot_dmg_bonus                += float(equip_stats.get("dot_dmg_bonus", 0.0))
        burn_dmg_bonus               += float(equip_stats.get("burn_dmg_bonus", 0.0))
        bleed_dmg_bonus              += float(equip_stats.get("bleed_dmg_bonus", 0.0))
        poison_dmg_bonus             += float(equip_stats.get("poison_dmg_bonus", 0.0))
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
        loot_qty_bonus              += float(equip_stats.get("loot_qty_bonus", 0.0))
        loot_luck_bonus             += float(equip_stats.get("loot_luck_bonus", 0.0))
        for elem, val in (equip_stats.get("damage_taken_convert_pct") or {}).items():
            damage_taken_convert_pct[elem] = damage_taken_convert_pct.get(elem, 0.0) + float(val)
        for elem, val in (equip_stats.get("element_dmg_bonus") or {}).items():
            element_dmg_bonus[elem] = element_dmg_bonus.get(elem, 0.0) + float(val)
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
        freeze_on_skill    = freeze_on_skill   or bool(equip_stats.get("freeze_on_skill", False))
        freeze_on_skill_chance += float(equip_stats.get("freeze_on_skill_chance", 0.0))
        poison_immunity    = poison_immunity   or bool(equip_stats.get("poison_immunity", False))
        debuff_immune_pct += equip_stats.get("debuff_immune_pct", 0.0)
        heal_pct          += equip_stats.get("heal_pct", 0.0)
        cooldown_reduce   += equip_stats.get("cooldown_reduce", 0.0)

    # ── Formation MP reservation (applied last, after all bonuses) ────────────
    # Two reservation sources stack here, both bound by the same MAX cap:
    #   1. Per-formation gem reservation (from active formation slots' gems)
    #   2. Per-skill reservation (from formation skills in the player's bar)
    # The skill reservation replaces the old flat 8% base — bigger formations
    # cost more MP to channel, smaller ones less. Trận Đạo path reduction is
    # already applied inside each helper, so we just sum and re-cap.
    from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
    skill_reserve_pct = compute_formation_skill_reserve_pct(
        learned_skill_keys, formation_stages=formation_stages,
    )
    raw_reserve = float(form_bonuses.get("_mp_reserve_pct", 0.0)) + skill_reserve_pct
    reserve_pct = max(0.0, min(FORMATION_MAX_RESERVE_PCT, raw_reserve))
    mp_reserved = int(mp_max * reserve_pct)
    mp_max = max(0, mp_max - mp_reserved)

    # HP → Shield conversion: a fraction of the final hp_max becomes flat
    # shield_max_base, and hp_max shrinks by the same amount. Clamped at 0.95
    # so HP never drops to 0. Applied last so every other hp/shield bonus has
    # already been folded in.
    hp_to_shield_pct = max(0.0, min(0.95, hp_to_shield_pct))
    if hp_to_shield_pct > 0:
        converted = int(hp_max * hp_to_shield_pct)
        shield_max_base += converted
        hp_max = max(1, hp_max - converted)

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
        freeze_on_skill_chance=freeze_on_skill_chance,
        poison_immunity=poison_immunity,
        debuff_immune_pct=debuff_immune_pct,
        burn_stack_cap=DEFAULT_BURN_STACK_CAP + burn_stack_cap_bonus,
        burn_per_stack_pct=DEFAULT_BURN_PER_STACK_PCT + burn_per_stack_pct_bonus,
        bonus_dmg_vs_burn=bonus_dmg_vs_burn,
        dot_can_crit=dot_can_crit,
        bleed_stack_cap=DEFAULT_BLEED_STACK_CAP + bleed_stack_cap_bonus,
        bleed_per_stack_pct=DEFAULT_BLEED_PER_STACK_PCT + bleed_per_stack_pct_bonus,
        bleed_on_hit_pct=bleed_on_hit_pct,
        bleed_heal_reduce=bleed_heal_reduce,
        crit_rating_vs_bleed=crit_rating_vs_bleed,
        crit_dmg_vs_bleed=crit_dmg_vs_bleed,
        true_dmg_pct=true_dmg_pct,
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
        shield_recharge_delay=max(0, DEFAULT_SHIELD_RECHARGE_DELAY + shield_recharge_delay_bonus),
        damage_bonus_from_shield_pct=damage_bonus_from_shield_pct,
        thorn_pct=thorn_pct,
        thorn_from_shield=thorn_from_shield,
        stun_on_hit_pct=stun_on_hit_pct,
        shock_stack_cap=DEFAULT_SHOCK_STACK_CAP + shock_stack_cap_bonus,
        shock_per_stack_pct=DEFAULT_SHOCK_PER_STACK_PCT + shock_per_stack_pct_bonus,
        shock_on_hit_pct=shock_on_hit_pct,
        turn_steal_pct=turn_steal_pct,
        mark_on_hit_pct=mark_on_hit_pct,
        damage_bonus_from_evasion_pct=damage_bonus_from_evasion_pct,
        crit_rating_vs_marked=crit_rating_vs_marked,
        crit_dmg_vs_marked=crit_dmg_vs_marked,
        silence_on_crit_pct=silence_on_crit_pct,
        heal_reduce_on_hit_pct=heal_reduce_on_hit_pct,
        cleanse_on_turn_pct=cleanse_on_turn_pct,
        barrier_on_cleanse=barrier_on_cleanse,
        heal_can_crit=heal_can_crit,
        soul_drain_on_hit_pct=soul_drain_on_hit_pct,
        stat_steal_on_hit_pct=stat_steal_on_hit_pct,
        crit_rating_vs_drained=crit_rating_vs_drained,
        dot_dmg_bonus=dot_dmg_bonus,
        burn_dmg_bonus=burn_dmg_bonus,
        bleed_dmg_bonus=bleed_dmg_bonus,
        poison_dmg_bonus=poison_dmg_bonus,
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
        loot_qty_bonus=loot_qty_bonus,
        loot_luck_bonus=loot_luck_bonus,
        damage_taken_convert_pct=damage_taken_convert_pct,
        element_dmg_bonus=element_dmg_bonus,
        element_pen=element_pen,
        mp_reserved=mp_reserved,
        mp_reserve_pct=reserve_pct,
        resistances=resistances,
    )
