"""Hỏa Linh Căn — Bạo Liệt (on-hit burn).

Base 20% chance on hit to apply Thiêu Đốt; chance and duration both scale
with Hỏa Linh Căn level (every 3 levels = +1 turn).
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.constants.effects import EffectKey
from src.game.constants.linh_can import LINH_CAN_DATA, scaled_proc_chance
from src.game.systems.combatant import Combatant

_BASE_PROC_CHANCE = 0.20
_BASE_DURATION = 2
_PROC_PER_LEVEL = float(LINH_CAN_DATA["hoa"]["scaling"]["proc_chance_per_level"])


def on_hit(actor: Combatant, target: Combatant, dmg: int, rng: random.Random, log: list[str]) -> None:
    """Apply burn debuff if proc triggers."""
    if "hoa" not in actor.linh_can or dmg <= 0:
        return
    level = actor.linh_can_levels.get("hoa", 1) if actor.linh_can_levels else 1
    chance = scaled_proc_chance(level, _BASE_PROC_CHANCE, _PROC_PER_LEVEL)
    if rng.random() < chance:
        duration = _BASE_DURATION + max(0, level - 1) // 3
        target.apply_effect(EffectKey.DEBUFF_THIEU_DOT, duration)
        log.append(
            f"    🔥 **{actor.name}** [Hỏa Lv{level}] Bạo Liệt — Thiêu Đốt {duration} lượt!"
        )
