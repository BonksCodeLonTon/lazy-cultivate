"""Item quality tiers shared across crafting systems (forge, alchemy, ...).

Quality (Hoàng / Huyền / Địa / Thiên) is an item-level rarity that scales
numeric outputs (e.g. equipment implicit stats, pill effect magnitude) and
may unlock special display labels. The distribution is rolled per-craft
from a recipe's ``quality_chances`` map with an optional comprehension
bonus that shifts probability toward Thiên.
"""
from __future__ import annotations

import random


QUALITY_LABELS: dict[str, str] = {
    "hoan": "Hoàng Phẩm",
    "huyen": "Huyền Phẩm",
    "dia": "Địa Phẩm",
    "thien": "Thiên Phẩm",
}

# Special effects applied on top of a base roll.
#   implicit_mult  — multiplies the rolled numeric output (stats, effect magnitude)
#   affix_floor    — fraction of range used as new minimum (forge-only; 0 = no floor)
#   guaranteed_max — one affix rolls at exactly its grade max (forge-only)
#   special_label  — display label shown on the item
QUALITY_SPECIAL: dict[str, dict] = {
    "hoan":  {
        "implicit_mult": 1.0,
        "affix_floor": 0.0,
        "guaranteed_max": False,
        "special_label": None,
    },
    "huyen": {
        "implicit_mult": 1.15,
        "affix_floor": 0.0,
        "guaranteed_max": False,
        "special_label": "✦ Linh Khí Tăng Cường",
    },
    "dia":   {
        "implicit_mult": 1.30,
        "affix_floor": 0.5,
        "guaranteed_max": False,
        "special_label": "✦✦ Thiên Địa Tinh Hoa",
    },
    "thien": {
        "implicit_mult": 1.50,
        "affix_floor": 0.75,
        "guaranteed_max": True,
        "special_label": "✦✦✦ Vô Thượng Thần Vật",
    },
}

QUALITY_ORDER: tuple[str, ...] = ("hoan", "huyen", "dia", "thien")

# Comprehension bonus caps at +10% thien chance.
_COMPREHENSION_BONUS_PER_POINT = 0.0005
_COMPREHENSION_BONUS_CAP = 0.10


def roll_quality(quality_chances: dict[str, float], comprehension: int = 0) -> str:
    """Roll a quality tier from a chances map, with a soft comprehension bonus.

    ``quality_chances`` maps tier key → relative weight. The function is
    tolerant to weights that do not sum to 1 — they are renormalised.
    ``comprehension`` (Character.stats.comprehension) adds up to +10% to the
    Thiên weight before renormalisation.
    """
    bonus = min(comprehension * _COMPREHENSION_BONUS_PER_POINT, _COMPREHENSION_BONUS_CAP)
    chances = dict(quality_chances)
    chances["thien"] = min(chances.get("thien", 0.0) + bonus, 1.0)

    total = sum(chances.values())
    if total <= 0:
        return "hoan"
    roll = random.random() * total
    cumulative = 0.0
    for quality, weight in chances.items():
        cumulative += weight
        if roll <= cumulative:
            return quality
    return "hoan"


def quality_tier_index(quality: str) -> int:
    """Return 1-based tier index for a quality key (hoan=1..thien=4)."""
    try:
        return QUALITY_ORDER.index(quality) + 1
    except ValueError:
        return 1


def quality_from_tier_index(tier: int) -> str:
    """Reverse of :func:`quality_tier_index` — 1..4 → quality key."""
    idx = max(1, min(len(QUALITY_ORDER), tier)) - 1
    return QUALITY_ORDER[idx]


def implicit_multiplier(quality: str) -> float:
    """Return the implicit/output multiplier for a quality tier."""
    return QUALITY_SPECIAL.get(quality, QUALITY_SPECIAL["hoan"])["implicit_mult"]


def special_label(quality: str) -> str | None:
    """Return the decorative label for a quality tier (or None for Hoàng)."""
    return QUALITY_SPECIAL.get(quality, QUALITY_SPECIAL["hoan"])["special_label"]
