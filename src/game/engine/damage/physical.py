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


def apply_armor_to_elemental(
    dmg: int,
    attack_type: str,
    def_stat: int,
    extension_pct: float,
    pen_pct: float = 0.0,
) -> int:
    """Hộ Pháp extension — same armor formula applied to non-physical hits.

    Fires only when:
      * ``attack_type != "physical"`` (physical already handled upstream)
      * ``extension_pct > 0`` (defender carries the formation flag or buff)
      * ``def_stat > 0`` (light kits get nothing)

    The base armor reduction is computed with the SAME formula and cap as
    ``apply_physical_defense`` (so this step has identical diminishing
    returns and can never exceed the physical cap), then scaled by
    ``extension_pct``. Stacks multiplicatively WITH ``apply_elemental``'s
    resistance step downstream — that's the design goal: armor and resist
    are separate lanes, both apply to the same hit.
    """
    if attack_type == "physical" or extension_pct <= 0 or def_stat <= 0:
        return dmg
    effective_def = def_stat * max(0.0, 1.0 - pen_pct)
    base_reduction = min(
        MAX_PHYS_REDUCTION,
        effective_def / (effective_def + PHYS_DEF_K),
    )
    reduction = base_reduction * max(0.0, min(1.0, extension_pct))
    return max(0, int(dmg * (1.0 - reduction)))
