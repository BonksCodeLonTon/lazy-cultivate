"""Rating → percentage conversion.

Formula: % = Rating / (Rating + K)  where K = 1300
Example: 300 CritRating → 300 / (300 + 1300) = 18.75%
"""

K = 1300

BASE_CRIT_CHANCE = 0.05       # 5% base crit
BASE_CRIT_DMG_MULT = 1.5      # 150% base crit damage
BASE_EVASION = 0.0


def rating_to_pct(rating: int) -> float:
    return rating / (rating + K)


def crit_chance(crit_rating: int, crit_res_rating: int = 0) -> float:
    raw = BASE_CRIT_CHANCE + rating_to_pct(crit_rating)
    reduction = rating_to_pct(crit_res_rating)
    return max(0.0, min(raw - reduction, 0.75))  # cap at 75%


def evasion_chance(evasion_rating: int) -> float:
    return BASE_EVASION + rating_to_pct(evasion_rating)


def crit_dmg_multiplier(crit_dmg_rating: int) -> float:
    return BASE_CRIT_DMG_MULT + rating_to_pct(crit_dmg_rating)
