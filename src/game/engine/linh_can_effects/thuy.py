"""Thủy Linh Căn — Ngưng Đọng (on-hit slow).

15% chance on hit to apply −25% SPD for 2 turns.
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.systems.combatant import Combatant

_PROC_CHANCE = 0.15
_DURATION = 2
_SPD_REDUCE_PCT = 25


def on_hit(actor: Combatant, target: Combatant, dmg: int, rng: random.Random, log: list[str]) -> None:
    """Apply slow debuff if proc triggers."""
    if "thuy" not in actor.linh_can or dmg <= 0:
        return
    if rng.random() < _PROC_CHANCE:
        target.apply_effect("EffectNgungDong", _DURATION)
        log.append(f"    💧 **{actor.name}** [Thủy] Ngưng Đọng — -{_SPD_REDUCE_PCT}% tốc {_DURATION} lượt!")
