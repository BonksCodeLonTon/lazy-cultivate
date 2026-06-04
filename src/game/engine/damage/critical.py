"""Critical hit step.

Rolls whether the hit crits and applies the crit damage multiplier.
"""
from __future__ import annotations

import random

from src.game.constants.balance import MAX_CRIT_CHANCE
from src.game.engine.rating import crit_chance, crit_dmg_multiplier


def apply_critical(
    raw: int,
    crit_rating: int,
    crit_res_rating: int,
    crit_dmg_rating: int,
    rng: random.Random,
    force_crit: bool = False,
    bonus_crit_chance: float = 0.0,
    bonus_crit_dmg_mult: float = 0.0,
) -> tuple[int, bool]:
    """Return (damage_after_crit, is_crit).

    ``force_crit=True`` skips the chance roll and guarantees a crit — used
    when the target is incapacitated (Đông Băng / freeze): the next skill
    landing on a frozen target auto-crits regardless of crit_rating vs.
    crit_res_rating.

    ``bonus_crit_chance`` / ``bonus_crit_dmg_mult`` are conditional, target-
    state amps (e.g. Thái Bạch Canh Kim's anti-bleed hunt). Both default to
    0.0, so every existing call site that omits them resolves bit-for-bit to
    the original behaviour. The chance amp stacks onto the rating-derived
    chance and is clamped to ``MAX_CRIT_CHANCE``; the dmg amp adds to the crit
    multiplier (only matters when the hit actually crits).
    """
    if force_crit:
        is_crit = True
    else:
        chance = crit_chance(crit_rating, crit_res_rating) + bonus_crit_chance
        if chance > MAX_CRIT_CHANCE:
            chance = MAX_CRIT_CHANCE
        is_crit = rng.random() < chance
    mult = (
        crit_dmg_multiplier(crit_dmg_rating) + bonus_crit_dmg_mult
        if is_crit
        else 1.0
    )
    return int(raw * mult), is_crit
