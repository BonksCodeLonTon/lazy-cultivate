"""Critical hit step.

Rolls whether the hit crits and applies the crit damage multiplier.
"""
from __future__ import annotations

import random

from src.game.engine.rating import crit_chance, crit_dmg_multiplier


def apply_critical(
    raw: int,
    crit_rating: int,
    crit_res_rating: int,
    crit_dmg_rating: int,
    rng: random.Random,
    force_crit: bool = False,
) -> tuple[int, bool]:
    """Return (damage_after_crit, is_crit).

    ``force_crit=True`` skips the chance roll and guarantees a crit — used
    when the target is incapacitated (Đông Băng / freeze): the next skill
    landing on a frozen target auto-crits regardless of crit_rating vs.
    crit_res_rating.
    """
    if force_crit:
        is_crit = True
    else:
        is_crit = rng.random() < crit_chance(crit_rating, crit_res_rating)
    mult = crit_dmg_multiplier(crit_dmg_rating) if is_crit else 1.0
    return int(raw * mult), is_crit
