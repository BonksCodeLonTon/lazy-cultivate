"""Final damage bonus step.

Applies the attacker's additive final-damage percentage bonus.
"""
from __future__ import annotations


def apply_final_bonus(dmg: int, final_dmg_bonus: float) -> int:
    """Return damage after applying final_dmg_bonus multiplier."""
    return int(dmg * (1.0 + final_dmg_bonus))
