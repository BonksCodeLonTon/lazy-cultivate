"""Consume-and-burst skill effects.

Each function consumes a resource pool on actor (shield, mana stacks) and
deals a scaled burst as elemental damage. The legacy ``burst_burn``
(detonating burn stacks via ``ConsumeBurnBurst``) was removed — fire
detonates now use the generic ``auto_cast_on_stacks`` mechanic in
``skill_extras`` instead. Shock stacks remain (separate damage-amplifier
strategy, not a burst-on-cast).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.balance import MAX_ELEMENTAL_RES
from src.game.engine.damage import colorize_damage
from src.game.systems.combatant import Combatant

if TYPE_CHECKING:
    from .session import CombatSession


def apply_elem_res(raw: int, target: Combatant, element: str, pen: float = 0.0) -> int:
    """Apply target's (penetrated) elemental resistance to a raw burst amount."""
    res = max(0.0, target.resistances.get(element, 0.0) - pen)
    return max(1, int(raw * (1.0 - min(MAX_ELEMENTAL_RES, res))))


def burst_shield(
    session: "CombatSession", actor: Combatant, target: Combatant, skill_data: dict
) -> None:
    """Consume the entire shield pool for burst damage (thổ resistance applies)."""
    shield_amt = actor.consume_shield()
    if shield_amt <= 0:
        return
    mult = float(skill_data.get("burst_shield_mult", 1.5))
    dmg = apply_elem_res(max(1, int(shield_amt * mult)), target, "tho")
    target.take_damage(dmg)
    session.log.append(
        f"    🪨💥 **Thổ Tường Bùng Nổ!** tiêu {shield_amt:,} khiên → "
        f"{colorize_damage(f'-{dmg:,} HP', 'tho')} ({target.hp:,}/{target.hp_max:,})"
    )


def burst_mana_stacks(
    session: "CombatSession", actor: Combatant, target: Combatant, skill_data: dict
) -> None:
    """Consume all mana stacks → thủy burst damage scaled by stacks × mp_max."""
    stacks = actor.consume_mana_stacks()
    if stacks <= 0:
        return
    per_stack_mult = float(skill_data.get("burst_per_mana_stack_mult", 0.12))
    raw = max(1, int(actor.mp_max * per_stack_mult * stacks))
    dmg = apply_elem_res(raw, target, "thuy")
    target.take_damage(dmg)
    session.log.append(
        f"    💧💥 **Linh Khí Bùng Nổ!** nổ {stacks} tầng → "
        f"{colorize_damage(f'-{dmg:,} HP', 'thuy')} ({target.hp:,}/{target.hp_max:,})"
    )
