"""Base damage roll step.

Formula: raw = (base_dmg + mp_cost + atk * dmg_scale.atk + matk * dmg_scale.matk)
              * uniform(0.85, 1.15)

A skill may scale with ATK, MATK, or both simultaneously.
"""
from __future__ import annotations

import random

from src.game.models.skill import DmgScale


def roll_base(
    base_dmg: int,
    mp_cost: int,
    rng: random.Random,
    atk: int = 0,
    matk: int = 0,
    dmg_scale: DmgScale | None = None,
) -> int:
    """Roll base damage with ±15% variance.

    atk        — attacker ATK stat.
    matk       — attacker MATK stat.
    dmg_scale  — per-stat scaling (atk and matk fractions).
    """
    scale = dmg_scale or DmgScale()
    scaled = int(atk * scale.atk) + int(matk * scale.matk)
    base = base_dmg + mp_cost + scaled
    return int(base * rng.uniform(0.85, 1.15))
