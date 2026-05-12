"""Elemental resistance step — percentage-based reduction.

Resistance values are fractions (0.0 = no resist, ``MAX_ELEMENTAL_RES`` = cap).
Penetration reduces the effective resistance before capping.

After resistance, an optional ``<element>_damage_taken`` multiplier amplifies
the result so a debuff like Xích Luyện Tỏa Hồn (+15% fire damage taken at the
10-gem tier) can bump incoming fire damage without touching the defender's
resistance. Floors at 0 (a hypothetical -1.0 modifier wipes the hit) and the
1-damage minimum still wins so the skill registers as a hit.
"""
from __future__ import annotations

from src.game.constants.balance import MAX_ELEMENTAL_RES


def apply_elemental(
    dmg: int,
    element: str | None,
    defender_res: dict[str, float],
    pen_pct: float = 0.0,
    damage_taken_by_element: dict[str, float] | None = None,
) -> int:
    """Return damage after elemental resistance + ``<element>_damage_taken`` amp."""
    if not element:
        return dmg
    res_pct = 0.0
    if element in defender_res:
        res_pct = max(0.0, min(MAX_ELEMENTAL_RES, defender_res[element] * (1.0 - pen_pct)))
    after_res = dmg * (1.0 - res_pct)
    if damage_taken_by_element:
        amp = float(damage_taken_by_element.get(element, 0.0))
        if amp:
            after_res *= max(0.0, 1.0 + amp)
    return max(1, int(after_res))
