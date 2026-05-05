"""Combat-hit damage helpers.

Wraps the pure damage pipeline with effective-stat construction and
post-pipeline scaling that depend on live combatant state (stacks,
active buff/debuff modifiers, res shreds, HP/MP/shield/mana pools).

Keeping these here keeps combat.py focused on sequencing and logging
while all damage math lives under ``engine/damage``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from src.game.constants.balance import (
    MAX_ELEMENTAL_RES,
    MAX_FINAL_DMG_REDUCE,
    SPD_EVASION_BASELINE,
    SPD_EVASION_CAP,
    SPD_EVASION_PER_POINT,
)
from src.game.constants.effects import EffectKey
from src.game.engine.stats import AttackStats, DefenseStats

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


def spd_evasion_bonus(spd: int) -> int:
    """Flat evasion-rating bonus derived from SPD (capped).

    Single source of truth for SPD→evasion conversion — used both by
    build_defense_stats (defender's effective evasion) and by
    apply_damage_scaling (Phong damage_bonus_from_evasion_pct scaling).
    """
    raw = max(0, (spd - SPD_EVASION_BASELINE) * SPD_EVASION_PER_POINT)
    return min(SPD_EVASION_CAP, raw)


# Per-element shred is now read directly from ``actor.element_res_shred``
# (the generic dict). The legacy 7 flat fields have been removed.


def build_attack_stats(
    actor: "Combatant", target: "Combatant", actor_mods: dict,
    skill_element: str | None = None,
) -> AttackStats:
    """Build effective AttackStats for one hit.

    Applies the actor's active buff mods plus target-state vulnerabilities:
      - ``bonus_dmg_vs_burn`` when the target has burn stacks
      - shock stacks on the target amplify final_dmg_bonus per stack, but only
        for Lôi-element hits (lightning payload resonates with the shock)
      - ``crit_rating_vs_bleed`` / ``crit_dmg_vs_bleed`` vs bleeding targets
      - ``crit_rating_vs_marked`` / ``crit_dmg_vs_marked`` vs Phong-Ấn targets
      - frozen target (DEBUFF_DONG_BANG): force_crit = True. The pipeline
        skips the crit roll and the freeze is consumed by the hit's caller.
      - ``dmg_bonus_<elem>`` from effects only counts when the outgoing skill
        matches that element (e.g. BuffHoaThan boosts Hỏa skills only).
    """
    final_dmg_bonus = actor.final_dmg_bonus + actor_mods.get("final_dmg_bonus", 0.0)
    # Hào Quang Củng Cố — each accumulated fortify stack also boosts outgoing
    # final damage (matches the symmetric DR contribution in
    # ``effective_damage_reduction``). Stacks tick up during periodic phase.
    if actor.fortify_per_turn_pct > 0 and actor.fortify_stacks > 0:
        final_dmg_bonus += actor.fortify_per_turn_pct * actor.fortify_stacks
    if skill_element:
        final_dmg_bonus += actor_mods.get(f"dmg_bonus_{skill_element}", 0.0)
        # Permanent per-element dmg bonus from constitutions / equipment
        # (generic dict pickup — replaces per-element flat fields).
        final_dmg_bonus += float(actor.element_dmg_bonus.get(skill_element, 0.0))
    if target.burn_stacks > 0 and actor.bonus_dmg_vs_burn > 0:
        final_dmg_bonus += actor.bonus_dmg_vs_burn
    if (
        skill_element == "loi"
        and target.shock_stacks > 0
        and target.shock_per_stack_pct > 0
    ):
        final_dmg_bonus += target.shock_stacks * target.shock_per_stack_pct

    crit_rating = actor.crit_rating + int(actor_mods.get("crit_rating", 0))
    crit_dmg_rating = actor.crit_dmg_rating + int(actor_mods.get("crit_dmg_rating", 0))
    if target.bleed_stacks > 0:
        crit_rating += actor.crit_rating_vs_bleed
        crit_dmg_rating += actor.crit_dmg_vs_bleed
    if target.has_effect(EffectKey.DEBUFF_AN_PHONG):
        crit_rating += actor.crit_rating_vs_marked
        crit_dmg_rating += actor.crit_dmg_vs_marked
    if target.hp_max_drained > 0:
        crit_rating += actor.crit_rating_vs_drained

    force_crit = target.has_effect(EffectKey.DEBUFF_DONG_BANG)

    # ATK / MATK scale with current Energy Shield: depleted shield = depleted
    # punch. Constitutions like Nguyên Linh Khiên Thể (mage) and Cương Khiên
    # Chiến Thể (physical) turn shield into a live offensive resource that
    # drains as it absorbs hits.
    effective_atk = actor.atk
    if actor.atk_from_shield_pct > 0 and actor.shield > 0:
        effective_atk += int(actor.shield * actor.atk_from_shield_pct)
    effective_matk = actor.matk
    if actor.matk_from_shield_pct > 0 and actor.shield > 0:
        effective_matk += int(actor.shield * actor.matk_from_shield_pct)

    return AttackStats(
        crit_rating=crit_rating,
        crit_dmg_rating=crit_dmg_rating,
        final_dmg_bonus=final_dmg_bonus,
        atk=effective_atk,
        matk=effective_matk,
        force_crit=force_crit,
    )


def build_defense_stats(
    target: "Combatant", target_mods: dict, actor: "Combatant",
    spd_evasion_bonus: Callable[[int], int],
) -> DefenseStats:
    """Build effective DefenseStats for one hit.

    Combines the target's base resistances with ``res_all`` / per-element
    mods from active effects and the attacker's elemental shreds. SPD-derived
    evasion bonus (``spd_evasion_bonus``) stacks with the target's base
    evasion_rating and active modifiers.
    """
    res_all_mod = target_mods.get("res_all", 0.0)
    effective_res: dict[str, float] = {}
    for elem, res in target.resistances.items():
        per_elem_mod = target_mods.get(f"res_{elem}", 0.0)
        shred = float(actor.element_res_shred.get(elem, 0.0))
        effective_res[elem] = max(0.0, min(MAX_ELEMENTAL_RES, res + res_all_mod + per_elem_mod - shred))

    effective_spd = max(1, round(target.spd * (1.0 + target_mods.get("spd_pct", 0.0))))
    return DefenseStats(
        evasion_rating=(
            target.evasion_rating
            + int(target_mods.get("evasion_rating", 0))
            + spd_evasion_bonus(effective_spd)
        ),
        crit_res_rating=target.crit_res_rating + int(target_mods.get("crit_res_rating", 0)),
        def_stat=target.def_stat,
        resistances=effective_res,
    )


def effective_damage_reduction(target: "Combatant", target_mods: dict) -> float:
    """Final damage reduction for the target, capped at MAX_FINAL_DMG_REDUCE.

    BuffBatTu and similar debuff mods stack additively with the base reduce.
    Hào Quang Củng Cố adds two contributions on top: (a) accumulated fortify
    stacks scale by ``fortify_per_turn_pct``, and (b) the post-hit brace adds
    ``fortify_post_hit_dr_pct`` while ``fortify_braced_turns > 0``. Both are
    capped along with everything else at ``MAX_FINAL_DMG_REDUCE``.
    """
    reduce = target.final_dmg_reduce + target_mods.get("final_dmg_reduce", 0.0)
    if target.fortify_per_turn_pct > 0 and target.fortify_stacks > 0:
        reduce += target.fortify_per_turn_pct * target.fortify_stacks
    if target.fortify_braced_turns > 0 and target.fortify_post_hit_dr_pct > 0:
        reduce += target.fortify_post_hit_dr_pct
    return min(MAX_FINAL_DMG_REDUCE, max(0.0, reduce))


def apply_damage_scaling(dmg: int, actor: "Combatant", actor_mods: dict) -> int:
    """Add flat bonuses from the actor's HP/MP/evasion/shield pools and mana stacks.

    Ordering: flat bonuses add first, then mana_stack_dmg_bonus applies as a
    multiplier so late stacks compound the scaled damage, not just base.
    Runs AFTER target damage reduction so HP/MP-scaling builds still deliver
    their power payoff through heavy DR.
    """
    hp_bonus = int(actor.hp_max * actor.damage_bonus_from_hp_pct)
    if hp_bonus > 0:
        dmg += hp_bonus
    mp_bonus = int(actor.mp_max * actor.damage_bonus_from_mp_pct)
    if mp_bonus > 0:
        dmg += mp_bonus
    if actor.damage_bonus_from_evasion_pct > 0:
        # Mirror the defender's effective-evasion calc so SPD-derived evasion
        # also feeds the Phong damage scaler — fast-attack Phong builds get
        # paid for stacking SPD instead of only base evasion_rating.
        eff_spd = max(1, round(actor.spd * (1.0 + actor_mods.get("spd_pct", 0.0))))
        eva_total = (
            actor.evasion_rating
            + int(actor_mods.get("evasion_rating", 0))
            + spd_evasion_bonus(eff_spd)
        )
        eva_bonus = int(eva_total * actor.damage_bonus_from_evasion_pct)
        if eva_bonus > 0:
            dmg += eva_bonus
    if actor.shield > 0 and actor.damage_bonus_from_shield_pct > 0:
        shield_bonus = int(actor.shield * actor.damage_bonus_from_shield_pct)
        if shield_bonus > 0:
            dmg += shield_bonus
    if actor.mana_stacks > 0 and actor.mana_stack_dmg_bonus > 0:
        stack_mult = 1.0 + (actor.mana_stacks * actor.mana_stack_dmg_bonus)
        dmg = int(dmg * stack_mult)
    return dmg
