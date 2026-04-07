"""Evasion check step.

Roll whether the attacker's hit is dodged by the defender's evasion rating.
"""
from __future__ import annotations

import random

from src.game.engine.rating import evasion_chance


def check_evasion(evasion_rating: int, rng: random.Random) -> bool:
    """Return True if the attack is evaded."""
    return rng.random() < evasion_chance(evasion_rating)
