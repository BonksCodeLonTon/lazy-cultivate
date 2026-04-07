"""Ám Linh Căn — Hủ Thể (on-hit lifesteal).

15% chance on hit to steal 30% of damage dealt as HP.
Phase: on_hit (called after damage is applied to target).
"""
from __future__ import annotations

import random

from src.game.systems.combatant import Combatant

_PROC_CHANCE = 0.15
_STEAL_PCT = 0.30


def on_hit(actor: Combatant, target: Combatant, dmg: int, rng: random.Random, log: list[str]) -> None:
    """Heal actor for a fraction of damage dealt if proc triggers."""
    if "am" not in actor.linh_can or dmg <= 0:
        return
    if rng.random() < _PROC_CHANCE:
        steal = int(dmg * _STEAL_PCT)
        actor.hp = min(actor.hp_max, actor.hp + steal)
        log.append(f"    🌑 **{actor.name}** [Ám] Hủ Thể — hút {steal:,} HP!")
