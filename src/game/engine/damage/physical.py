"""Physical defense reduction step.

Applies physical resistance derived from the defender's DEF stat.

Formula (diminishing returns):
    reduction = min(MAX_PHYS_REDUCTION, def_stat / (def_stat + PHYS_DEF_K))

Penetration reduces effective DEF before the formula is applied.
Magical and true-damage skills bypass this step entirely.
"""
from __future__ import annotations

from src.game.constants.balance import MAX_PHYS_REDUCTION, PHYS_DEF_K


def apply_physical_defense(
    dmg: int,
    attack_type: str,
    def_stat: int,
    pen_pct: float = 0.0,
) -> int:
    """Return damage after physical defense reduction.

    Only "physical" attack_type triggers the reduction; all others pass through.
    """
    if attack_type != "physical" or def_stat <= 0:
        return dmg
    effective_def = def_stat * max(0.0, 1.0 - pen_pct)
    reduction = min(MAX_PHYS_REDUCTION, effective_def / (effective_def + PHYS_DEF_K))
    return max(0, int(dmg * (1.0 - reduction)))
