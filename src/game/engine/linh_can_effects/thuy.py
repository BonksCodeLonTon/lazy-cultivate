"""Thủy Linh Căn — Ngưng Đọng (on-hit slow).

Base 15% chance on hit to apply −25% SPD; chance scales with Thủy Linh
Căn level, and every 4 levels adds 1 turn of duration.
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.constants.linh_can import LINH_CAN_DATA, scaled_proc_chance
from src.game.systems.combatant import Combatant

_BASE_PROC_CHANCE = 0.15
_BASE_DURATION = 2
_SPD_REDUCE_PCT = 25
_PROC_PER_LEVEL = float(LINH_CAN_DATA["thuy"]["scaling"]["proc_chance_per_level"])


def on_hit(actor: Combatant, target: Combatant, dmg: int, rng: random.Random, log: list[str]) -> None:
    """Apply slow debuff if proc triggers."""
    if "thuy" not in actor.linh_can or dmg <= 0:
        return
    level = actor.linh_can_levels.get("thuy", 1) if actor.linh_can_levels else 1
    chance = scaled_proc_chance(level, _BASE_PROC_CHANCE, _PROC_PER_LEVEL)
    if rng.random() < chance:
        duration = _BASE_DURATION + max(0, level - 1) // 4
        target.apply_effect("EffectNgungDong", duration)
        log.append(
            f"    💧 **{actor.name}** [Thủy Lv{level}] Ngưng Đọng — "
            f"-{_SPD_REDUCE_PCT}% tốc {duration} lượt!"
        )
