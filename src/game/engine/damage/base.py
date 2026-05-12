"""Base damage roll step.

Formula:
    scaled_part = (atk × dmg_scale.atk + matk × dmg_scale.matk) × SKILL_STAT_SCALE_MULT
    crit_flat   = int(crit_dmg_rating × crit_dmg_rating_to_dmg_pct)
    raw         = (base_dmg + mp_cost + scaled_part + crit_flat)
                  × uniform(0.85, 1.15)

A skill may scale with ATK, MATK, or both simultaneously. ``base_dmg`` is
used as authored — power-tier scaling is baked into the value at design time
(higher-grade scrolls have larger base_dmg) rather than derived from a realm
multiplier. ``crit_flat`` is the hybrid crit→dmg scalar — only non-zero on
builds whose constitution / unique gear / linh-can grants
``crit_dmg_rating_to_dmg_pct``.
"""
from __future__ import annotations

import random

from src.game.constants.balance import SKILL_STAT_SCALE_MULT
from src.game.models.skill import DmgScale


def roll_base(
    base_dmg: int,
    mp_cost: int,
    rng: random.Random,
    atk: int = 0,
    matk: int = 0,
    dmg_scale: DmgScale | None = None,
    crit_dmg_rating: int = 0,
    crit_dmg_rating_to_dmg_pct: float = 0.0,
) -> int:
    """Roll base damage with ±15% variance.

    atk         — attacker ATK stat.
    matk        — attacker MATK stat.
    dmg_scale   — per-stat scaling (atk and matk fractions).
    crit_dmg_rating, crit_dmg_rating_to_dmg_pct — hybrid scalar: a fraction
                  of the attacker's crit-damage rating is added as flat
                  damage so a crit-stacked build gets a reliable floor in
                  addition to the chance-gated crit multiplier.
    """
    scale = dmg_scale or DmgScale()
    scaled = int((atk * scale.atk + matk * scale.matk) * SKILL_STAT_SCALE_MULT)
    crit_flat = (
        int(crit_dmg_rating * crit_dmg_rating_to_dmg_pct)
        if crit_dmg_rating_to_dmg_pct > 0 and crit_dmg_rating > 0
        else 0
    )
    base = int(base_dmg) + mp_cost + scaled + crit_flat
    return int(base * rng.uniform(0.85, 1.15))
