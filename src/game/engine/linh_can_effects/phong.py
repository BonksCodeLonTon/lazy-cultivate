"""Phong Linh Căn — Tốc Biến (pre-turn dodge).

10% chance to fully dodge an incoming attack before it resolves.
Phase: pre_turn (called on the *target* before the actor acts).
"""
from __future__ import annotations

import random

from src.game.systems.combatant import Combatant


def try_dodge(target: Combatant, rng: random.Random, log: list[str]) -> bool:
    """Return True if target dodges the entire incoming attack."""
    if "phong" not in target.linh_can:
        return False
    if rng.random() < 0.10:
        log.append(f"  🌪️ **{target.name}** [Phong] Tốc Biến — né tránh hoàn toàn!")
        return True
    return False
