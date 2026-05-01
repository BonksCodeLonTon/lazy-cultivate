"""Kim Linh Căn — Xuyên Thấu (pre-damage penetration).

Base 20% chance to ignore 30% of the target's elemental resistance; both
the proc chance and the penetration scale up with Kim Linh Căn level.
Phase: pre_damage (called on the *actor* before calculate_damage).
"""
from __future__ import annotations

import random

from src.game.constants.linh_can import LINH_CAN_DATA, scaled_proc_chance
from src.game.systems.combatant import Combatant

_BASE_PEN_PCT = 0.30
_PEN_PER_LEVEL = 0.025   # +2.5% extra resist-shred per level above 1
_BASE_PROC_CHANCE = 0.20
_PROC_PER_LEVEL = float(LINH_CAN_DATA["kim"]["scaling"]["proc_chance_per_level"])


def get_pen_pct(actor: Combatant, rng: random.Random, log: list[str]) -> float:
    """Return penetration fraction for this attack (0.0 if no proc)."""
    if "kim" not in actor.linh_can:
        return 0.0
    level = actor.linh_can_levels.get("kim", 1) if actor.linh_can_levels else 1
    chance = scaled_proc_chance(level, _BASE_PROC_CHANCE, _PROC_PER_LEVEL)
    if rng.random() < chance:
        pen = _BASE_PEN_PCT + max(0, level - 1) * _PEN_PER_LEVEL
        log.append(f"  ⚙️ **{actor.name}** [Kim Lv{level}] Xuyên Thấu kích hoạt!")
        return pen
    return 0.0
