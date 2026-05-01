"""Phong Linh Căn — Tốc Biến (pre-turn dodge).

Base 10% chance to fully dodge; chance scales gently with Phong Linh Căn
level (additive per-level boost).
Phase: pre_turn (called on the *target* before the actor acts).
"""
from __future__ import annotations

import random

from src.game.constants.linh_can import LINH_CAN_DATA, scaled_proc_chance
from src.game.systems.combatant import Combatant

_BASE_PROC_CHANCE = 0.10
_PROC_PER_LEVEL = float(LINH_CAN_DATA["phong"]["scaling"]["proc_chance_per_level"])


def try_dodge(target: Combatant, rng: random.Random, log: list[str]) -> bool:
    """Return True if target dodges the entire incoming attack."""
    if "phong" not in target.linh_can:
        return False
    level = target.linh_can_levels.get("phong", 1) if target.linh_can_levels else 1
    chance = scaled_proc_chance(level, _BASE_PROC_CHANCE, _PROC_PER_LEVEL)
    if rng.random() < chance:
        log.append(f"  🌪️ **{target.name}** [Phong Lv{level}] Tốc Biến — né tránh hoàn toàn!")
        return True
    return False
