"""Đan Độc (pill toxicity) — penalty curves for cultivation and combat.

Players accumulate ``dan_doc`` by consuming pills (see ``alchemy.consume_pill``)
and reduce it via purification pills (``effect_key: "reduce_toxicity"``).
This module is the single source of truth for what that accumulated stat
*does* once it's on the player — every reader in cultivation / combat goes
through ``toxicity_factor`` so a balance change here ripples everywhere.

Curve: linear from 0 at ``dan_doc=0`` to 1.0 at ``TOXICITY_FULL`` (1000).
Past saturation the factor stays at 1.0 — the player can't be "extra
poisoned" beyond max penalty.
"""
from __future__ import annotations


# Saturation point: dan_doc value at which all penalties hit their max.
# Tuned to typical pill economy — most pills give +2 to +5 dan_doc per
# consume after quality reduction, so 200 ≈ 40 Hoàn-quality body pills
# (≈1-2 realms of heavy pill use). Past this point penalties stay flat.
TOXICITY_FULL: int = 200

# Per-axis penalty maxima (applied at factor=1.0). Each is a fractional
# subtraction from the matching multiplier — never the floor itself.
TOXICITY_CULT_SPEED_PENALTY_MAX: float = 0.50  # −50 % cultivation EXP at full
TOXICITY_FINAL_DMG_PENALTY_MAX:  float = 0.25  # −25 % outgoing damage at full
TOXICITY_HP_REGEN_PENALTY_MAX:   float = 0.50  # −50 % HP regen pct at full

# Floor for the cultivation multiplier so a max-toxicity player still
# gains *some* EXP (otherwise progress halts entirely while detoxing).
MIN_CULT_SPEED_MULT: float = 0.10


def toxicity_factor(dan_doc: int) -> float:
    """Map a raw ``dan_doc`` value to the [0.0, 1.0] penalty factor."""
    if dan_doc <= 0:
        return 0.0
    return min(1.0, dan_doc / TOXICITY_FULL)


def cult_speed_penalty(dan_doc: int) -> float:
    """Subtractive penalty applied to the cultivation EXP multiplier.

    Returned as a positive number — caller subtracts it from the base
    ``1.0 + cultivation_speed_bonus`` mult.
    """
    return toxicity_factor(dan_doc) * TOXICITY_CULT_SPEED_PENALTY_MAX


def final_dmg_penalty(dan_doc: int) -> float:
    """Subtractive penalty applied to the player's ``final_dmg_bonus``.

    A 0.25 result means a final_dmg_bonus of +0.10 becomes -0.15 — the
    body's outgoing damage drops 15 % below baseline.
    """
    return toxicity_factor(dan_doc) * TOXICITY_FINAL_DMG_PENALTY_MAX


def hp_regen_multiplier(dan_doc: int) -> float:
    """Multiplicative scalar applied to ``hp_regen_pct``.

    Toxicity reduces *the rate* of regen rather than subtracting from it,
    so a player with no regen still gets nothing (no negative regen) and
    a player with high regen sees a meaningful slowdown.
    """
    return max(0.0, 1.0 - toxicity_factor(dan_doc) * TOXICITY_HP_REGEN_PENALTY_MAX)


def pill_exp_multiplier(dan_doc: int) -> float:
    """Scale pill-granted EXP by ``1 − toxicity_factor`` — full XP when clean,
    **zero** XP at saturation.

    Harsher than the turn-based cultivation penalty (which floors at 50 %)
    because pill-driven cultivation is supposed to be the optional shortcut
    you pay for with toxicity. At Mãn Độc the body literally can't absorb
    more pill essence — every pill XP source (exp_luyen_the / exp_qi /
    breakthrough_* / lure_beast / repel_beast) zeros out until the player
    detoxes. Combat-buff pills (``buff_*``) don't go through this multiplier
    since they grant permanent stat counters, not XP.
    """
    return max(0.0, 1.0 - toxicity_factor(dan_doc))


def tier_label(dan_doc: int) -> str:
    """Human-readable tier label for UI display."""
    f = toxicity_factor(dan_doc)
    if f <= 0.0:
        return "Thanh Khiết"
    if f < 0.30:
        return "Khinh Độc"
    if f < 0.60:
        return "Trúng Độc"
    if f < 1.0:
        return "Trầm Độc"
    return "Mãn Độc"
