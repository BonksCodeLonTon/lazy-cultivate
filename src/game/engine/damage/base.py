"""Base damage roll step.

Formula: raw = (base_dmg + mp_cost) * uniform(0.85, 1.15)
"""
from __future__ import annotations

import random


def roll_base(base_dmg: int, mp_cost: int, rng: random.Random) -> int:
    """Roll base damage with ±15% variance."""
    return int((base_dmg + mp_cost) * rng.uniform(0.85, 1.15))
