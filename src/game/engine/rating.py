"""Rating → percentage conversion.

Formula: % = rating / (rating + RATING_K)
Example: 300 CritRating → 300 / (300 + 1300) = 18.75%
"""
from src.game.constants.balance import (
    RATING_K,
    BASE_CRIT_CHANCE,
    MAX_CRIT_CHANCE,
    BASE_CRIT_DMG_MULT,
    BASE_EVASION,
)


def rating_to_pct(rating: int) -> float:
    return rating / (rating + RATING_K)


def crit_chance(crit_rating: int, crit_res_rating: int = 0) -> float:
    raw = BASE_CRIT_CHANCE + rating_to_pct(crit_rating)
    reduction = rating_to_pct(crit_res_rating)
    return max(0.0, min(raw - reduction, MAX_CRIT_CHANCE))


def evasion_chance(evasion_rating: int) -> float:
    return BASE_EVASION + rating_to_pct(evasion_rating)


def crit_dmg_multiplier(crit_dmg_rating: int) -> float:
    return BASE_CRIT_DMG_MULT + rating_to_pct(crit_dmg_rating)
