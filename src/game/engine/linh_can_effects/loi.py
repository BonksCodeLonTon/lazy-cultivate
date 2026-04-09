"""Lôi Linh Căn — Tê Liệt (on-crit stun).

12% chance when the hit is a critical strike to stun target 1 turn.
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.constants.effects import EffectKey
from src.game.systems.combatant import Combatant

_PROC_CHANCE = 0.12
_DURATION = 1


def on_hit(
    actor: Combatant,
    target: Combatant,
    dmg: int,
    is_crit: bool,
    rng: random.Random,
    log: list[str],
) -> None:
    """Apply stun debuff if crit proc triggers."""
    if "loi" not in actor.linh_can or dmg <= 0 or not is_crit:
        return
    if rng.random() < _PROC_CHANCE:
        target.apply_effect(EffectKey.CC_STUN, _DURATION)
        log.append(f"    ⚡ **{actor.name}** [Lôi] Tê Liệt — choáng {_DURATION} lượt!")
