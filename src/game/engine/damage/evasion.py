"""Evasion check step.

Roll whether the attacker's hit is dodged by the defender's evasion rating,
optionally counter-scaled by the attacker's accuracy rating.
"""
from __future__ import annotations

import random

from src.game.engine.rating import evasion_chance, rating_to_pct


def check_evasion(
    evasion_rating: int,
    rng: random.Random,
    accuracy_rating: int = 0,
) -> bool:
    """Return True if the attack is evaded.

    ``accuracy_rating`` mirrors the crit_rating ↔ crit_res_rating relationship
    — the attacker's accuracy converts via the same rating formula and is
    subtracted from the defender's evasion chance. Floored at 0 so accuracy
    can't push the dodge chance below zero (and indirectly lower than the
    BASE_EVASION baseline produces).
    """
    chance = evasion_chance(evasion_rating)
    if accuracy_rating > 0:
        chance = max(0.0, chance - rating_to_pct(accuracy_rating))
    return rng.random() < chance
