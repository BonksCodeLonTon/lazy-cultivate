"""Hỏa Linh Căn — Bạo Liệt (on-hit burn).

20% chance on hit to apply Thiêu Đốt (4% HP/turn × 2 turns).
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.constants.effects import EffectKey
from src.game.systems.combatant import Combatant

_PROC_CHANCE = 0.20
_DURATION = 2


def on_hit(actor: Combatant, target: Combatant, dmg: int, rng: random.Random, log: list[str]) -> None:
    """Apply burn debuff if proc triggers."""
    if "hoa" not in actor.linh_can or dmg <= 0:
        return
    if rng.random() < _PROC_CHANCE:
        target.apply_effect(EffectKey.DEBUFF_THIEU_DOT, _DURATION)
        log.append(f"    🔥 **{actor.name}** [Hỏa] Bạo Liệt — Thiêu Đốt {_DURATION} lượt!")
