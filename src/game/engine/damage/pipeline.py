"""Damage calculation pipeline.

Chains all steps:
  evasion (defender) → base roll (ATK/MATK scaling) → crit
  → physical defense (physical only) → elemental resistance → final bonus

Call site supplies two DTOs:
  AttackStats  — attacker's offensive stats (crit, atk/matk, final_dmg_bonus)
  DefenseStats — defender's defensive stats (evasion, crit_res, def_stat, resistances)

This keeps attacker and defender concerns cleanly separated and makes the
pipeline independent of Combatant internals, so equipment bonuses (future) only
need to touch Combatant fields — nothing here changes.
"""
from __future__ import annotations

import random

from src.game.engine.stats import AttackStats, DefenseStats
from src.game.models.skill import Skill

from .base import roll_base
from .critical import apply_critical
from .elemental import apply_elemental
from .evasion import check_evasion
from .final_bonus import apply_final_bonus
from .physical import apply_physical_defense
from .result import DamageResult


def calculate_damage(
    skill: Skill,
    attacker: AttackStats,
    defender: DefenseStats,
    rng: random.Random | None = None,
    pen_pct: float = 0.0,
) -> DamageResult:
    """Run the full damage pipeline for one skill hit.

    pen_pct — attacker's penetration (0–1); reduces elemental and physical defenses.
    """
    rng = rng or random.Random()

    # Defender tries to dodge (evasion is a defender property)
    if check_evasion(defender.evasion_rating, rng):
        return DamageResult(raw=0, final=0, is_crit=False, is_evaded=True, element=skill.element)

    raw = roll_base(
        skill.base_dmg, skill.mp_cost, rng,
        atk=attacker.atk, matk=attacker.matk, dmg_scale=skill.dmg_scale,
    )
    dmg, is_crit = apply_critical(
        raw, attacker.crit_rating, defender.crit_res_rating, attacker.crit_dmg_rating, rng
    )
    # Physical defense — diminishing returns formula, capped at 75%, bypassed by magical/true
    dmg = apply_physical_defense(dmg, str(skill.attack_type), defender.def_stat, pen_pct)
    dmg = apply_elemental(dmg, skill.element, defender.resistances, pen_pct)
    dmg = apply_final_bonus(dmg, attacker.final_dmg_bonus)

    return DamageResult(raw=raw, final=dmg, is_crit=is_crit, is_evaded=False, element=skill.element)
