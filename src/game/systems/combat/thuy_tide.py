"""Huyền Thủy Trường Sinh — Tide-reservoir discharge strike helper.

The body banks a fraction of damage TAKEN into its OWN reservoir
(``Combatant.thuy_intake_reservoir`` — deliberately isolated from the skill
``SkillAtkThuyTrieuTichLang``'s ``thuy_tide`` pool) and later spends it as a
Thủy strike: Glacial Shatter (L6) and the Tidal Flood (L9) both release a chunk
of the reservoir as elemental damage on the opponent.

``tide_strike`` reuses the exact mitigation math the existing tide / overheal
reservoirs use (``_tide_charge_discharge`` in cast_consumers.py:312-323 and
``_overheal_release`` in overheal_reservoir.py:97-99): the released amount is
scaled by ``1 + attacker.final_dmg_bonus`` and the target's Thủy resistance
(net of the attacker's Thủy penetration), clamped to ``MAX_ELEMENTAL_RES``.
Thủy-element, resistance-able, NOT true damage.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.balance import MAX_ELEMENTAL_RES

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


def tide_strike(
    attacker: "Combatant", target: "Combatant", released: int,
    element: str = "thuy",
) -> int:
    """Deal ``released`` (post-reservoir) damage to ``target`` as an elemental
    strike, applying the standard tide/overheal mitigation, and return the
    actual damage dealt.

    Mirrors ``_tide_charge_discharge`` / ``_overheal_release``: scale by
    ``1 + attacker.final_dmg_bonus``, reduce by the target's element resistance
    net of the attacker's element penetration (capped at ``MAX_ELEMENTAL_RES``),
    floor at 1. No-op (returns 0) when ``released`` is non-positive or the
    target is already down — callers handle reservoir bookkeeping.
    """
    if released <= 0 or not target.is_alive():
        return 0
    target_res = max(
        0.0,
        min(
            MAX_ELEMENTAL_RES,
            target.resistances.get(element, 0.0)
            - attacker.element_pen.get(element, 0.0),
        ),
    )
    mult = 1.0 + attacker.final_dmg_bonus
    strike = max(1, int(released * mult * (1.0 - target_res)))
    target.take_damage(strike)
    return strike
