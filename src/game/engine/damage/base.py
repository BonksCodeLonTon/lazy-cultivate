"""Base damage roll step.

Formula:
    realm_mult  = max(1, skill_realm) × SKILL_BASE_DMG_REALM_MULT
    scaled_part = (atk × dmg_scale.atk + matk × dmg_scale.matk) × SKILL_STAT_SCALE_MULT
    raw         = (base_dmg × realm_mult + mp_cost + scaled_part) × uniform(0.85, 1.15)

A skill may scale with ATK, MATK, or both simultaneously.
"""
from __future__ import annotations

import random

from src.game.constants.balance import SKILL_BASE_DMG_REALM_MULT, SKILL_STAT_SCALE_MULT
from src.game.models.skill import DmgScale


def roll_base(
    base_dmg: int,
    mp_cost: int,
    rng: random.Random,
    atk: int = 0,
    matk: int = 0,
    dmg_scale: DmgScale | None = None,
    skill_realm: int = 1,
) -> int:
    """Roll base damage with ±15% variance.

    atk         — attacker ATK stat.
    matk        — attacker MATK stat.
    dmg_scale   — per-stat scaling (atk and matk fractions).
    skill_realm — skill realm tier (1–9); higher realms multiply ``base_dmg``
                  linearly so a R9 skill's flat component is 9× a R1 skill's.
    """
    scale = dmg_scale or DmgScale()
    realm_mult = max(1, int(skill_realm)) * SKILL_BASE_DMG_REALM_MULT
    scaled = int((atk * scale.atk + matk * scale.matk) * SKILL_STAT_SCALE_MULT)
    base = int(base_dmg * realm_mult) + mp_cost + scaled
    return int(base * rng.uniform(0.85, 1.15))
