"""Ám Linh Căn — Hủ Thể (on-hit lifesteal).

Base 15% chance on hit to lifesteal 30% of damage dealt. Both the proc
chance and the steal % scale with Ám Linh Căn level.
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.constants.linh_can import LINH_CAN_DATA, scaled_proc_chance
from src.game.systems.combatant import Combatant

_BASE_PROC_CHANCE = 0.15
_BASE_STEAL_PCT = 0.30
_STEAL_PER_LEVEL = 0.025  # +2.5% steal per level above 1
_PROC_PER_LEVEL = float(LINH_CAN_DATA["am"]["scaling"]["proc_chance_per_level"])


def on_hit(actor: Combatant, target: Combatant, dmg: int, rng: random.Random, log: list[str]) -> None:
    """Heal actor for a fraction of damage dealt if proc triggers."""
    if "am" not in actor.linh_can or dmg <= 0:
        return
    level = actor.linh_can_levels.get("am", 1) if actor.linh_can_levels else 1
    chance = scaled_proc_chance(level, _BASE_PROC_CHANCE, _PROC_PER_LEVEL)
    if rng.random() < chance:
        steal_pct = _BASE_STEAL_PCT + max(0, level - 1) * _STEAL_PER_LEVEL
        steal = int(dmg * steal_pct)
        actor.hp = min(actor.hp_max, actor.hp + steal)
        log.append(
            f"    🌑 **{actor.name}** [Ám Lv{level}] Hủ Thể — hút {steal:,} HP!"
        )
