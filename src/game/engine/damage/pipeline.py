"""Damage calculation pipeline.

Chains all steps:
  evasion → base roll (with ATK/MATK scaling) → crit
  → physical defense (physical only) → elemental resistance → final bonus
"""
from __future__ import annotations

import random

from src.game.models.character import CharacterStats
from src.game.models.skill import AttackType, Skill

from .base import roll_base
from .critical import apply_critical
from .elemental import apply_elemental
from .evasion import check_evasion
from .final_bonus import apply_final_bonus
from .physical import apply_physical_defense
from .result import DamageResult


def calculate_damage(
    skill: Skill,
    attacker: CharacterStats,
    defender_res: dict[str, int],
    defender_crit_res_rating: int,
    rng: random.Random | None = None,
    pen_pct: float = 0.0,
    defender_def: int = 0,
) -> DamageResult:
    """Run the full damage pipeline.

    defender_def — target's DEF stat; used only for physical skills.
    """
    rng = rng or random.Random()

    if check_evasion(getattr(attacker, "evasion_rating", 0), rng):
        return DamageResult(raw=0, final=0, is_crit=False, is_evaded=True, element=skill.element)

    # Select attack power based on skill type (physical → ATK, magical → MATK)
    attack_power = attacker.atk if skill.attack_type == AttackType.PHYSICAL else attacker.matk

    raw = roll_base(skill.base_dmg, skill.mp_cost, rng, attack_power, skill.atk_scale)
    dmg, is_crit = apply_critical(
        raw, attacker.crit_rating, defender_crit_res_rating, attacker.crit_dmg_rating, rng
    )
    # Physical defense (diminishing returns, capped 75%) — bypassed by magical/true skills
    dmg = apply_physical_defense(dmg, str(skill.attack_type), defender_def, pen_pct)
    dmg = apply_elemental(dmg, skill.element, defender_res, pen_pct)
    dmg = apply_final_bonus(dmg, attacker.final_dmg_bonus)

    return DamageResult(raw=raw, final=dmg, is_crit=is_crit, is_evaded=False, element=skill.element)
