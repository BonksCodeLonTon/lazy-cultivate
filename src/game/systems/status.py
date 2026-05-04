"""Status snapshot — assembles every stat the /status embed needs.

Pure data shuffling: takes a player ORM, calls into the existing systems
(``compute_combat_stats``, ``compute_equipment_stats``, registry lookups),
and returns a flat dict ready for ``character_embed`` plus the parsed
Linh Căn list. Discord layer just wraps it.
"""
from __future__ import annotations

from src.data.registry import registry
from src.db.repositories.player_repo import _player_to_model
from src.game.constants.linh_can import parse_linh_can
from src.game.constants.realms import realm_label
from src.game.engine.equipment import compute_equipment_stats
from src.game.systems.character_stats import (
    active_formation_gem_keys,
    active_formation_gem_map,
    compute_combat_stats,
)
from src.game.systems.cultivation import get_active_formations
from src.game.systems.the_chat import get_constitutions


def build_status_snapshot(player) -> tuple[dict, list[str]]:
    """Assemble every value the /status embed needs.

    Returns ``(stats, linh_can_list)``. ``stats`` is the flat dict consumed
    by ``character_embed``; ``linh_can_list`` is the parsed root list the
    cog renders as a separate field.
    """
    char = _player_to_model(player)
    gem_keys = active_formation_gem_keys(player)
    gem_map = active_formation_gem_map(player)
    gem_count = len(gem_keys)

    equipped_instances = [
        i for i in (player.item_instances or []) if i.location == "equipped"
    ]
    equip_stats = compute_equipment_stats(equipped_instances)
    learned_skill_keys = [s.skill_key for s in (player.skills or [])]

    cs = compute_combat_stats(
        char,
        gem_count=gem_count,
        equip_stats=equip_stats,
        gem_keys=gem_keys,
        gem_keys_by_formation=gem_map,
        learned_skill_keys=learned_skill_keys,
    )

    equipped_const_keys = get_constitutions(player.constitution_type)
    const_names = [
        (registry.get_constitution(k) or {}).get("vi", k)
        for k in equipped_const_keys
    ]
    active_formation_keys = get_active_formations(player.active_formation)
    active_formation_names = [
        (registry.get_formation(k) or {}).get("vi", k)
        for k in active_formation_keys
    ]

    equipped_by_slot: dict[str, str] = {
        inst.slot: inst.display_name for inst in equipped_instances
    }
    linh_can_list = parse_linh_can(player.linh_can or "")

    stats: dict = {
        "hp_current": player.hp_current,
        "hp_max":     cs.hp_max,
        "mp_current": player.mp_current,
        "mp_max":     cs.mp_max,
        "spd":        cs.spd,
        "body_realm":      player.body_realm,
        "body_level":      player.body_level,
        "body_xp":         player.body_xp,
        "qi_realm":        player.qi_realm,
        "qi_level":        player.qi_level,
        "qi_xp":           player.qi_xp,
        "formation_realm": player.formation_realm,
        "formation_level": player.formation_level,
        "formation_xp":    player.formation_xp,
        "active_axis":     player.active_axis,
        "body_realm_label":      realm_label("body",      player.body_realm,      player.body_xp),
        "qi_realm_label":        realm_label("qi",        player.qi_realm,        player.qi_xp),
        "formation_realm_label": realm_label("formation", player.formation_realm, player.formation_xp),
        "merit":             player.merit,
        "karma_accum":       player.karma_accum,
        "primordial_stones": player.primordial_stones,
        "constitution":      " · ".join(const_names) if const_names else player.constitution_type,
        "active_formation":  " · ".join(active_formation_names) if active_formation_names else None,
        "gem_count":         gem_count,
        "mp_reserved":       cs.mp_reserved,
        "mp_reserve_pct":    cs.mp_reserve_pct,
        "atk":             cs.atk,
        "matk":            cs.matk,
        "def_stat":        cs.def_stat,
        "crit_rating":     cs.crit_rating,
        "crit_dmg_rating": cs.crit_dmg_rating,
        "evasion_rating":  cs.evasion_rating,
        "crit_res_rating": cs.crit_res_rating,
        "final_dmg_bonus": cs.final_dmg_bonus,
        "resistances":     {k: v for k, v in cs.resistances.items() if v > 0.0},
        "equipped_by_slot": equipped_by_slot,
    }
    return stats, linh_can_list
