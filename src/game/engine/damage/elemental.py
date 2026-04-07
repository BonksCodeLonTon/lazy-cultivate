"""Elemental resistance step.

Applies defender's elemental resistance, reduced by attacker's penetration %.
"""
from __future__ import annotations


def apply_elemental(
    dmg: int,
    element: str | None,
    defender_res: dict[str, int],
    pen_pct: float = 0.0,
) -> int:
    """Return damage after elemental resistance (minus penetration)."""
    if not element or element not in defender_res:
        return dmg
    effective_res = int(defender_res[element] * (1.0 - pen_pct))
    return max(0, dmg - effective_res)
