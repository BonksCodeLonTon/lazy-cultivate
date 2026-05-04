"""Combatant factories — lift a Character or an enemy/boss JSON entry into a
runtime ``Combatant`` ready for ``CombatSession``.

Kept separate from the session so callers that only need to spawn combatants
(previews, tribulation setup, admin commands) don't pull in the full fight
pipeline.
"""
from __future__ import annotations

from dataclasses import asdict, fields

from src.data.registry import registry
from src.game.constants.balance import (
    ENEMY_BASE_ELEM_RES, ENEMY_DMG_BONUS_SCALE, ENEMY_HP_SCALE_FACTOR,
    ENEMY_RANK_BASE_ATK, ENEMY_RANK_BASE_DEF, ENEMY_RANK_BASE_EVASION,
    ENEMY_RANK_BASE_MATK, ENEMY_REALM_LEVEL_STAT_MULT, ENEMY_SCALE_MAX,
)
from src.game.models.character import Character
from src.game.systems.combatant import Combatant


def build_player_combatant(
    char: Character,
    player_skill_keys: list[str],
    gem_count: int = 0,
    equip_stats: dict | None = None,
    gem_keys: list[str] | None = None,
    gem_keys_by_formation: dict[str, list[str]] | None = None,
) -> Combatant:
    """Build a Combatant from a Character dataclass.

    Delegates all stat math to compute_combat_stats — single source of truth.
    Multi-slot Trận Tu callers should pass ``gem_keys_by_formation`` so each
    formation's threshold bonuses attach to the right gem set.
    """
    from src.game.systems.character_stats import compute_combat_stats
    from src.game.systems.cultivation import get_active_formations

    cs = compute_combat_stats(
        char, gem_count=gem_count, equip_stats=equip_stats,
        gem_keys=gem_keys, gem_keys_by_formation=gem_keys_by_formation,
        learned_skill_keys=list(player_skill_keys),
    )

    # Formation skills — every active slot contributes its signature skill
    # to a SEPARATE bar that fires in parallel to the main cast each turn
    # (see ``CombatSession._fire_formation_skills``). Kept out of the main
    # rotation so multi-slot Trận Tu truly has all its formations active
    # simultaneously rather than time-sharing a single skill slot.
    final_skill_keys = list(player_skill_keys)
    formation_skill_keys: list[str] = []
    for active_key in get_active_formations(char.active_formation):
        form_data = registry.get_formation(active_key)
        if not form_data:
            continue
        frm_skill = form_data.get("formation_skill_key")
        if frm_skill and frm_skill not in formation_skill_keys:
            formation_skill_keys.append(frm_skill)

    hp_current = min(char.hp_current, cs.hp_max) if char.hp_current > 0 else cs.hp_max
    mp_current = min(char.mp_current, cs.mp_max) if char.mp_current > 0 else cs.mp_max

    # Carry every field CombatStats and Combatant share — field names match
    # by construction (see character_stats.CombatStats). Any CombatStats-only
    # fields (mp_reserved, mp_reserve_pct) are filtered out automatically.
    combatant_fields = {f.name for f in fields(Combatant)}
    cs_kwargs = {k: v for k, v in asdict(cs).items() if k in combatant_fields}

    return Combatant(
        key="player",
        name=char.name,
        hp=hp_current,
        mp=mp_current,
        element=None,
        skill_keys=final_skill_keys,
        formation_skill_keys=formation_skill_keys,
        linh_can=list(char.linh_can),
        linh_can_levels=dict(getattr(char, "linh_can_levels", {}) or {}),
        **cs_kwargs,
    )


def build_enemy_combatant(enemy_key: str, player_realm_total: int) -> Combatant | None:
    """Build enemy Combatant scaled to player realm, using per-enemy hp_scale.

    realm_level (1-10) in enemy data applies an additional combat-stat multiplier on top
    of the existing player-realm-based scaling, letting enemies within the same rank feel
    meaningfully different in power.
    """
    # Tribulations share the same Combatant shape as enemies (base_hp, base_spd,
    # element, skill_keys, ...) but live in a separate registry dict so they
    # can't be summoned through farming/dungeon paths. Fall back here so callers
    # can build either a regular enemy or a Thiên Kiếp from a single key.
    # Bypass get_tribulation's default_heavenly_trib auto-fallback so unrelated
    # missing keys still return None instead of silently becoming a Thiên Kiếp.
    enemy_data = registry.get_enemy(enemy_key) or registry.tribulations.get(enemy_key)
    if not enemy_data:
        return None

    rank = enemy_data.get("rank", "pho_thong")
    realm_scale = 1.0 + (player_realm_total / ENEMY_SCALE_MAX) * ENEMY_HP_SCALE_FACTOR
    hp_scale = enemy_data.get("hp_scale", 1.0)
    hp = int(enemy_data["base_hp"] * realm_scale * hp_scale)
    spd = enemy_data.get("base_spd", 8)
    enemy_dmg_bonus = (player_realm_total / ENEMY_SCALE_MAX) * ENEMY_DMG_BONUS_SCALE

    # Additional multiplier from realm_level (1-10) — differentiates enemies within same rank
    realm_level = enemy_data.get("realm_level", 1)
    rl_mult = ENEMY_REALM_LEVEL_STAT_MULT.get(realm_level, 1.0)

    # Combat stats: rank-based fallback × player-realm scale × realm-level scale
    atk          = int(enemy_data.get("base_atk",     ENEMY_RANK_BASE_ATK.get(rank,     30)) * realm_scale * rl_mult)
    matk         = int(enemy_data.get("base_matk",    ENEMY_RANK_BASE_MATK.get(rank,    30)) * realm_scale * rl_mult)
    def_stat     = int(enemy_data.get("base_def",     ENEMY_RANK_BASE_DEF.get(rank,     20)) * realm_scale * rl_mult)
    evasion_rating = int(enemy_data.get("base_evasion", ENEMY_RANK_BASE_EVASION.get(rank, 0)) * realm_scale * rl_mult)

    res: dict[str, float] = {}
    elem = enemy_data.get("element")
    # Per-enemy resistance overrides: ``base_res: {kim: 0.50, hoa: 0.15}``
    # in the enemy JSON layers on top of the default own-element res so
    # late-dungeon and themed bosses can carry meaningfully heavier
    # resistance profiles. Each entry is clamped to ``res_cap_pct`` (also
    # JSON-tunable; defaults to 0.75 to leave a 25% damage floor).
    res_cap = float(enemy_data.get("res_cap_pct", 0.75))
    if elem:
        # Default own-element res scales with player realm, capped at 35%.
        res[elem] = min(0.35, ENEMY_BASE_ELEM_RES * realm_scale)
    for r_elem, r_val in (enemy_data.get("base_res") or {}).items():
        # Stack JSON-supplied res additively on top of the default (so a
        # Linh Căn apex with ``base_res: {kim: 0.40}`` ends up at 40%
        # instead of being silently overwritten by the 10%-default).
        merged = res.get(r_elem, 0.0) + float(r_val)
        res[r_elem] = max(0.0, min(res_cap, merged))

    # Enemies regen MP so they can keep casting skills across a long fight.
    # Without this, their small MP pool empties after a handful of turns and
    # they fall back to auto-attacks for the rest of combat.
    mp_max = hp // 2
    enemy_mp_regen_pct = enemy_data.get("mp_regen_pct", 0.04)
    # HP regen for themed enemies (Dược Viên wood-spirits carry a regen aura).
    enemy_hp_regen_pct = enemy_data.get("hp_regen_pct", 0.0)

    # Optional combat-rating overrides from JSON — scale like atk/matk/def so
    # a "base_crit_rating": 80 at R9 turns into real crit chance after the
    # realm multipliers. Lets us put threat on individual apex enemies
    # instead of moving global rank fallbacks.
    # Crit ratings scale like atk/matk — realm × rl_mult.
    crit_rating = int(enemy_data.get("base_crit_rating", 0) * realm_scale * rl_mult)
    crit_dmg_rating = int(enemy_data.get("base_crit_dmg_rating", 0) * realm_scale * rl_mult)
    # JSON final_dmg_bonus stacks flat on top of the realm-based
    # enemy_dmg_bonus. Keep it small — realm scaling already contributes
    # +150-470 % before this field is added.
    extra_fdb = float(enemy_data.get("final_dmg_bonus", 0.0))

    return Combatant(
        key=enemy_key,
        name=enemy_data["vi"],
        hp=hp,
        hp_max=hp,
        mp=mp_max,
        mp_max=mp_max,
        spd=spd,
        element=elem,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        evasion_rating=evasion_rating,
        crit_rating=crit_rating,
        crit_dmg_rating=crit_dmg_rating,
        resistances=res,
        skill_keys=enemy_data.get("skill_keys", []),
        final_dmg_bonus=enemy_dmg_bonus + extra_fdb,
        mp_regen_pct=enemy_mp_regen_pct,
        hp_regen_pct=enemy_hp_regen_pct,
        immune_hard_cc=bool(enemy_data.get("immune_hard_cc", False)),
    )


def build_world_boss_combatant(
    boss_data: dict, current_hp: int, player_realm_total: int
) -> Combatant:
    """Build a persistent world-boss Combatant from a world_bosses.json entry.

    Unlike regular enemies, world bosses:
    - Persist HP across player attacks (passed in via ``current_hp``)
    - Always set ``immune_hard_cc=True`` so hard CC never lands
    - Use a fixed, pre-defined ``skill_pool`` instead of realm-scaled enemy skills
    - Scale their raw stats with the attacking player's realm so every fight stays challenging
    """
    rank = "chi_ton"   # World bosses are always supreme-rank for stat lookups
    realm_scale = 1.0 + (player_realm_total / ENEMY_SCALE_MAX) * 0.5

    hp_max = int(boss_data["base_hp"] * boss_data.get("hp_scale", 1.0))
    hp = max(0, min(current_hp, hp_max)) if current_hp else hp_max

    atk = int(ENEMY_RANK_BASE_ATK.get(rank, 100) * realm_scale * 1.5)
    matk = int(ENEMY_RANK_BASE_MATK.get(rank, 100) * realm_scale * 1.5)
    def_stat = int(ENEMY_RANK_BASE_DEF.get(rank, 60) * realm_scale * 1.5)
    evasion_rating = int(ENEMY_RANK_BASE_EVASION.get(rank, 0) * realm_scale)
    enemy_dmg_bonus = (player_realm_total / ENEMY_SCALE_MAX) * ENEMY_DMG_BONUS_SCALE

    elem = boss_data.get("element")
    res: dict[str, float] = {}
    if elem:
        res[elem] = min(0.40, ENEMY_BASE_ELEM_RES * realm_scale * 1.2)

    mp_max = max(800, hp_max // 4)

    return Combatant(
        key=boss_data["key"],
        name=boss_data["vi"],
        hp=hp,
        hp_max=hp_max,
        mp=mp_max,
        mp_max=mp_max,
        spd=boss_data.get("base_spd", 15),
        element=elem,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        evasion_rating=evasion_rating,
        resistances=res,
        skill_keys=list(boss_data.get("skill_pool", [])),
        final_dmg_bonus=enemy_dmg_bonus,
        mp_regen_pct=0.08,
        immune_hard_cc=True,
        is_world_boss=True,      # blocks hp_max mutations (Âm soul-drain, etc.)
        final_dmg_reduce=0.15,   # World bosses shrug off 15% of damage by default
    )
