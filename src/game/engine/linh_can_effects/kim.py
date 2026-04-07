"""Kim Linh Căn — Xuyên Thấu (pre-damage penetration).

20% chance to ignore 30% of the target's elemental resistance.
Phase: pre_damage (called on the *actor* before calculate_damage).
"""
from __future__ import annotations

import random

from src.game.systems.combatant import Combatant

_PEN_PCT = 0.30
_PROC_CHANCE = 0.20


def get_pen_pct(actor: Combatant, rng: random.Random, log: list[str]) -> float:
    """Return penetration fraction (0.0 or 0.30) for this attack."""
    if "kim" not in actor.linh_can:
        return 0.0
    if rng.random() < _PROC_CHANCE:
        log.append(f"  ⚙️ **{actor.name}** [Kim] Xuyên Thấu kích hoạt!")
        return _PEN_PCT
    return 0.0
