"""Lôi Linh Căn — Tê Liệt (on-crit stun).

Base 12% chance on crit to stun the target. Chance scales with Lôi Linh
Căn level; every 5 levels adds 1 turn of stun duration.
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.constants.effects import EffectKey
from src.game.constants.linh_can import LINH_CAN_DATA, scaled_proc_chance
from src.game.systems.combatant import Combatant

_BASE_PROC_CHANCE = 0.12
_BASE_DURATION = 1
_PROC_PER_LEVEL = float(LINH_CAN_DATA["loi"]["scaling"]["proc_chance_per_level"])


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
    level = actor.linh_can_levels.get("loi", 1) if actor.linh_can_levels else 1
    chance = scaled_proc_chance(level, _BASE_PROC_CHANCE, _PROC_PER_LEVEL)
    if rng.random() < chance:
        duration = _BASE_DURATION + max(0, level - 1) // 5
        target.apply_effect(EffectKey.CC_STUN, duration)
        log.append(
            f"    ⚡ **{actor.name}** [Lôi Lv{level}] Tê Liệt — choáng {duration} lượt!"
        )
