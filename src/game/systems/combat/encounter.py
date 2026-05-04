"""Free-fight encounter logic — rank selection, enemy picking, elite upgrade roll.

Used by the ``/fight`` flow to choose which enemy to spawn and whether the
player encounters a higher-rank elite. Pure logic: takes an RNG and the
player's ``Character`` model, returns deterministic data — no Discord, no DB.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from src.data.registry import registry
from src.game.systems.dungeon import compute_realm_total

if TYPE_CHECKING:
    from src.game.models.character import Character


# Each rank "owns" a zone of cultivation stages (1 zone = 9 stages = 1 realm).
# Used to scale the elite-upgrade chance: the further into the zone the player
# is, the more likely a rank-1 fight gets bumped up.
RANK_ZONE: dict[str, int] = {
    "pho_thong": 1,
    "tinh_anh":  3,
    "cuong_gia": 4,
    "hung_manh": 6,
    "dai_nang":  7,
    "than_thu":  8,
    "tien_thu":  9,
    "chi_ton":   10,
}

RANK_NEXT: dict[str, str] = {
    "pho_thong": "tinh_anh",
    "tinh_anh":  "cuong_gia",
    "cuong_gia": "hung_manh",
    "hung_manh": "dai_nang",
    "dai_nang":  "than_thu",
    "than_thu":  "tien_thu",
    "tien_thu":  "chi_ton",
}

# Weighted distribution for "🎲 Ngẫu Nhiên" — heavy on the low end so a
# fresh player isn't constantly thrown at Chí Tôn.
_RANDOM_RANK_WEIGHTS: list[tuple[str, int]] = [
    ("pho_thong", 60),
    ("cuong_gia", 30),
    ("dai_nang",   9),
    ("chi_ton",    1),
]

MAX_UPGRADE_CHANCE = 0.30
ELITE_LOOT_MULTIPLIER = 1.5


def pick_random_enemy(rank: str | None, rng: random.Random) -> str | None:
    """Return an enemy_key from ``rank``'s pool, or weighted-random if rank is None."""
    if rank:
        pool = registry.enemies_by_rank(rank)
    else:
        choices, weights = zip(*_RANDOM_RANK_WEIGHTS)
        chosen_rank = rng.choices(list(choices), weights=list(weights), k=1)[0]
        pool = registry.enemies_by_rank(chosen_rank)
    return rng.choice(pool)["key"] if pool else None


def upgrade_chance(base_rank: str, char: "Character") -> float:
    """Probability (0.0–``MAX_UPGRADE_CHANCE``) of encountering a higher-rank elite.

    Scales linearly from 0 at the start of the rank's zone to the cap at the
    top of the zone. Beyond the zone it stays capped — no extra reward for
    over-leveling.
    """
    zone = RANK_ZONE.get(base_rank, 1)
    rt = compute_realm_total(char)
    zone_floor = (zone - 1) * 9
    level_in_zone = max(0, min(9, rt - zone_floor))
    return level_in_zone / 9 * MAX_UPGRADE_CHANCE


def roll_elite_upgrade(
    base_rank: str | None,
    char: "Character",
    rng: random.Random,
) -> tuple[str | None, float, bool]:
    """Roll once for the elite-upgrade promotion.

    Returns ``(actual_rank, loot_multiplier, is_elite)``.
    - ``base_rank=None`` (random) skips the roll entirely — the random tier
      already covers the full ladder.
    - If a promotion is rolled but the next-rank pool is empty (data gap),
      the original rank is kept so the fight still spawns.
    """
    if base_rank is None:
        return None, 1.0, False

    chance = upgrade_chance(base_rank, char)
    if chance > 0 and rng.random() < chance:
        next_rank = RANK_NEXT.get(base_rank)
        if next_rank and registry.enemies_by_rank(next_rank):
            return next_rank, ELITE_LOOT_MULTIPLIER, True
    return base_rank, 1.0, False
