"""Base damage roll step.

Formula: raw = (base_dmg + mp_cost + attack_power * atk_scale) * uniform(0.85, 1.15)
"""
from __future__ import annotations

import random


def roll_base(
    base_dmg: int,
    mp_cost: int,
    rng: random.Random,
    attack_power: int = 0,
    atk_scale: float = 0.0,
) -> int:
    """Roll base damage with ±15% variance.

    attack_power — attacker ATK (physical) or MATK (magical) stat.
    atk_scale    — fraction of attack_power added to flat base (e.g. 1.0 = 100%).
    """
    base = base_dmg + mp_cost + int(attack_power * atk_scale)
    return int(base * rng.uniform(0.85, 1.15))
