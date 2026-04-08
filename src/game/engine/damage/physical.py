"""Physical defense reduction step.

Applies physical resistance derived from the defender's DEF stat.

Formula (diminishing returns, capped at 75%):
    reduction_pct = min(0.75, def_stat / (def_stat + _K))

Penetration reduces effective DEF before the formula is applied.
Magical and true-damage skills bypass this step entirely.
"""
from __future__ import annotations

_K = 500.0  # DEF constant — 500 DEF → 50% reduction; tunable


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
    reduction = min(0.75, effective_def / (effective_def + _K))
    return max(0, int(dmg * (1.0 - reduction)))
