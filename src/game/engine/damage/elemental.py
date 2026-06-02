"""Elemental resistance step — percentage-based reduction.

Resistance values are fractions (0.0 = no resist, ``MAX_ELEMENTAL_RES`` = cap).
Penetration reduces the effective resistance before capping.

After resistance, two element-keyed amps can multiply the result:

  * ``attacker_element_amp`` — outgoing per-element amps from the attacker
    (e.g. Vạn Kiếm's Sword Heart stacks giving +5% per stack on kim casts).
    Applied so element-only buffs scale ONLY their matching element without
    polluting the generic ``final_dmg_bonus`` pool.

  * ``damage_taken_by_element`` — incoming per-element amps on the defender
    (e.g. Xích Luyện Tỏa Hồn +15% fire damage taken at the 10-gem tier).

Both stack additively per source. Floors at 0 (a hypothetical -1.0 modifier
wipes the hit) and the 1-damage minimum still wins so the skill registers
as a hit.
"""
from __future__ import annotations

from src.game.constants.balance import MAX_ELEMENTAL_RES


def apply_elemental(
    dmg: int,
    element: str | None,
    defender_res: dict[str, float],
    pen_pct: float = 0.0,
    damage_taken_by_element: dict[str, float] | None = None,
    attacker_element_amp: dict[str, float] | None = None,
) -> int:
    """Return damage after elemental resistance + per-element attacker/defender amps."""
    if not element:
        return dmg
    res_pct = 0.0
    if element in defender_res:
        res_pct = max(0.0, min(MAX_ELEMENTAL_RES, defender_res[element] * (1.0 - pen_pct)))
    after_res = dmg * (1.0 - res_pct)
    if attacker_element_amp:
        amp = float(attacker_element_amp.get(element, 0.0))
        if amp:
            after_res *= max(0.0, 1.0 + amp)
    if damage_taken_by_element:
        amp = float(damage_taken_by_element.get(element, 0.0))
        if amp:
            after_res *= max(0.0, 1.0 + amp)
    return max(1, int(after_res))
