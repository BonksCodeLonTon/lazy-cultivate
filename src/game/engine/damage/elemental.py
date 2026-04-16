"""Elemental resistance step — percentage-based reduction.

Resistance values are fractions (0.0 = no resist, 0.75 = 75% resist cap).
Penetration reduces the effective resistance before capping.
"""
from __future__ import annotations


def apply_elemental(
    dmg: int,
    element: str | None,
    defender_res: dict[str, float],
    pen_pct: float = 0.0,
) -> int:
    """Return damage after elemental resistance (percentage, capped at 75%)."""
    if not element or element not in defender_res:
        return dmg
    res_pct = max(0.0, min(0.75, defender_res[element] * (1.0 - pen_pct)))
    return max(1, int(dmg * (1.0 - res_pct)))
