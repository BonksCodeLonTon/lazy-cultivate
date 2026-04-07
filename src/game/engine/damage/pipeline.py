"""Damage calculation pipeline.

Chains all steps: evasion → base roll → crit → elemental resistance → final bonus.
"""
from __future__ import annotations

import random

from src.game.models.character import CharacterStats
from src.game.models.skill import Skill

from .base import roll_base
from .critical import apply_critical
from .elemental import apply_elemental
from .evasion import check_evasion
from .final_bonus import apply_final_bonus
from .result import DamageResult


def calculate_damage(
    skill: Skill,
    attacker: CharacterStats,
    defender_res: dict[str, int],
    defender_crit_res_rating: int,
    rng: random.Random | None = None,
    pen_pct: float = 0.0,
) -> DamageResult:
    rng = rng or random.Random()

    if check_evasion(getattr(attacker, "evasion_rating", 0), rng):
        return DamageResult(raw=0, final=0, is_crit=False, is_evaded=True, element=skill.element)

    raw = roll_base(skill.base_dmg, skill.mp_cost, rng)
    dmg, is_crit = apply_critical(
        raw, attacker.crit_rating, defender_crit_res_rating, attacker.crit_dmg_rating, rng
    )
    dmg = apply_elemental(dmg, skill.element, defender_res, pen_pct)
    dmg = apply_final_bonus(dmg, attacker.final_dmg_bonus)

    return DamageResult(raw=raw, final=dmg, is_crit=is_crit, is_evaded=False, element=skill.element)
