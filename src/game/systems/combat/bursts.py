"""Consume-and-burst skill effects.

Each function consumes a resource pool on actor or target (shield, mana stacks,
burn stacks) and deals a scaled burst as elemental damage. Log lines match the
original inline format so replays stay stable.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.effects import EffectKey
from src.game.engine.damage import colorize_damage
from src.game.systems.combatant import Combatant

if TYPE_CHECKING:
    from .session import CombatSession


def apply_elem_res(raw: int, target: Combatant, element: str, shred: float = 0.0) -> int:
    """Apply target's (shredded) elemental resistance to a raw burst amount."""
    res = max(0.0, target.resistances.get(element, 0.0) - shred)
    return max(1, int(raw * (1.0 - min(0.75, res))))


def burst_shield(
    session: "CombatSession", actor: Combatant, target: Combatant, skill_data: dict
) -> None:
    """Consume the entire shield pool for burst damage (thổ resistance applies)."""
    shield_amt = actor.consume_shield()
    if shield_amt <= 0:
        return
    mult = float(skill_data.get("burst_shield_mult", 1.5))
    dmg = apply_elem_res(max(1, int(shield_amt * mult)), target, "tho")
    target.hp = max(0, target.hp - dmg)
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
    target.hp = max(0, target.hp - dmg)
    session.log.append(
        f"    💧💥 **Linh Khí Bùng Nổ!** nổ {stacks} tầng → "
        f"{colorize_damage(f'-{dmg:,} HP', 'thuy')} ({target.hp:,}/{target.hp_max:,})"
    )


def burst_burn(
    session: "CombatSession", actor: Combatant, target: Combatant, skill_data: dict
) -> None:
    """Consume all burn stacks on target → hoa burst scaled by stacks × (base + matk×0.5)."""
    stacks = target.consume_burn_stacks()
    if stacks <= 0:
        return
    per_stack_mult = float(skill_data.get("burst_per_stack_mult", 0.35))
    base = skill_data.get("base_dmg", 0) + int(actor.matk * 0.5)
    raw = max(1, int(base * per_stack_mult * stacks))
    dmg = apply_elem_res(raw, target, "hoa", shred=actor.fire_res_shred)
    target.hp = max(0, target.hp - dmg)
    # Stacks are gone, clear the burn debuff marker too
    target.effects.pop(EffectKey.DEBUFF_THIEU_DOT, None)
    session.log.append(
        f"    🔥💥 **Hỏa Bùng Phát!** nổ {stacks} tầng Thiêu Đốt → "
        f"{colorize_damage(f'-{dmg:,} HP', 'hoa')} ({target.hp:,}/{target.hp_max:,})"
    )
