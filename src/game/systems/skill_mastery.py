"""Skill Mastery pure logic (Phase 1 core).

Deterministic, side-effect-free functions over the tables in
``src.game.constants.skill_mastery``. RNG is always injected by the caller
(``roll`` arguments) so these functions stay testable and reproducible — this
module never imports ``random``.
"""
from __future__ import annotations

from src.game.constants.skill_mastery import (
    GATES,
    HO_DAO_PHU_BONUS,
    MAX_LEVEL,
    POWER_MULT,
    VISIBLE_MAX,
    XP_TO_NEXT,
    BAND_RANGES,
    BAND_LABELS,
    MasteryBand,
)


def power_mult(level: int) -> float:
    """Skill power multiplier for ``level``, clamped to [1, MAX_LEVEL]."""
    clamped = max(1, min(level, MAX_LEVEL))
    return POWER_MULT[clamped]


# Dedicated, steeper mp-cost mastery curve — kept separate from ``power_mult``
# so MP outlay can be tuned independently of damage. mp_cost grows linearly
# ~+6%/level (×1.0 at lvl 1 → ×2.44 at lvl 25). Because the damage formula
# ``DMG = base + mp_cost`` reads mp_cost, a mastered skill costs more MP AND
# hits harder through that term.
_MP_COST_MULT_PER_LEVEL: float = 0.06


def mp_cost_mult(level: int) -> float:
    """Mastery mp-cost multiplier for ``level``, clamped to [1, MAX_LEVEL]."""
    clamped = max(1, min(level, MAX_LEVEL))
    return round(1.0 + _MP_COST_MULT_PER_LEVEL * (clamped - 1), 4)


def xp_to_next(level: int) -> int | None:
    """XP needed to advance from ``level``; None at gate ceilings/MAX.

    Returns None for 5/10/15/20 (item-gated breakthroughs) and 25 (MAX).
    """
    return XP_TO_NEXT.get(level)


def is_band_ceiling(level: int) -> bool:
    """True if ``level`` is a gated band ceiling (5, 10, 15, 20)."""
    return level in (5, 10, 15, 20)


def band_of(level: int) -> MasteryBand:
    """Map ``level`` to its MasteryBand (clamped to [1, MAX_LEVEL])."""
    clamped = max(1, min(level, MAX_LEVEL))
    for band, (low, high) in BAND_RANGES.items():
        if low <= clamped <= high:
            return band
    # Unreachable for clamped levels; satisfies the type checker.
    return MasteryBand.DANG_PHONG_TAO_CUC


def band_label(level: int) -> tuple[str, str]:
    """(vietnamese, english) label for the band containing ``level``."""
    return BAND_LABELS[band_of(level)]


def apply_combat_xp(level: int, xp: int, gained: int) -> tuple[int, int, bool]:
    """Add combat ``gained`` XP and roll up levels within the current band.

    Stops at a band ceiling (5/10/15/20) or MAX_LEVEL. WHY: ceilings are
    item-gated breakthroughs — XP alone cannot cross them. On reaching a
    ceiling, surplus XP is discarded (locked decision): returns the ceiling
    level with ``xp=0`` and ``at_ceiling=True``. Within a band, leftover XP
    carries into the next level normally.

    Returns ``(new_level, new_xp, at_ceiling)``.
    """
    new_level = level
    new_xp = xp + gained

    while True:
        needed = XP_TO_NEXT.get(new_level)
        if needed is None:
            # At a ceiling or MAX: no XP transition exists. Discard overshoot.
            return new_level, 0, True
        if new_xp < needed:
            return new_level, new_xp, False
        new_xp -= needed
        new_level += 1


def breakthrough_chance(gate_level: int, fails: int, ho_dao_phu: bool) -> float:
    """Success probability for the breakthrough at ``gate_level``.

    ``base_success + fails*pity_per_fail + Hộ Đạo Phù bonus``, clamped to
    [0.0, 1.0]. ``gate_level`` must be a key in GATES.
    """
    gate = GATES[gate_level]
    chance = gate["base_success"] + fails * gate["pity_per_fail"]
    if ho_dao_phu:
        chance += HO_DAO_PHU_BONUS
    return max(0.0, min(1.0, chance))


def resolve_breakthrough(
    gate_level: int,
    fails: int,
    ho_dao_phu: bool,
    dinh_dao_chau: bool,
    roll: float,
) -> dict:
    """Resolve one breakthrough attempt at ``gate_level`` against ``roll``.

    Caller supplies ``roll`` in [0, 1); success iff ``roll < chance``.

    - Success: level becomes ``gate_level + 1``, gate stack consumed, fails reset.
    - Fail: level unchanged, fails incremented, gate stack consumed UNLESS
      Định Đạo Châu refunds it.
    - Hộ Đạo Phù is consumed on every attempt; Định Đạo Châu only on a fail.
    """
    gate = GATES[gate_level]
    qty = gate["qty"]
    chance = breakthrough_chance(gate_level, fails, ho_dao_phu)
    success = roll < chance

    if success:
        return {
            "success": True,
            "new_level": gate_level + 1,
            "new_fails": 0,
            "consumed_gate_qty": qty,
            "refunded": False,
            "consumed_ho_dao_phu": ho_dao_phu,
            "consumed_dinh_dao_chau": False,
        }

    refunded = dinh_dao_chau
    return {
        "success": False,
        "new_level": gate_level,
        "new_fails": fails + 1,
        "consumed_gate_qty": 0 if refunded else qty,
        "refunded": refunded,
        "consumed_ho_dao_phu": ho_dao_phu,
        "consumed_dinh_dao_chau": dinh_dao_chau,
    }


def hidden_gate_revealed(
    level: int, holds_fruit: bool, dao_ti_unlocked: bool
) -> bool:
    """True iff the secret 20->21 gate is revealed.

    Requires the skill be at VISIBLE_MAX, the player hold the gate fruit, and
    Đạo Thai be unlocked (Nhập Thánh Cấp 9).
    """
    return level == VISIBLE_MAX and holds_fruit and dao_ti_unlocked


def progress_summary(level: int, xp: int, hidden_unlocked: bool = False) -> dict:
    """Pure UI snapshot of a single skill's mastery progression.

    ``cap`` exposes MAX_LEVEL only once the hidden path is in play
    (``hidden_unlocked`` flag set, or the skill already crossed past
    VISIBLE_MAX) — otherwise the visible ceiling is VISIBLE_MAX so the
    20->21 secret stays concealed. ``ratio`` is the bar fill in [0, 1];
    at a ceiling/cap (``xp_next is None``) it reads full.
    """
    vi, en = band_label(level)
    cap = MAX_LEVEL if (hidden_unlocked or level > VISIBLE_MAX) else VISIBLE_MAX
    xp_next = xp_to_next(level)
    if xp_next is None:
        ratio = 1.0
    else:
        ratio = max(0.0, min(1.0, xp / xp_next))
    return {
        "band_vi": vi,
        "band_en": en,
        "level": level,
        "cap": cap,
        "xp": xp,
        "xp_next": xp_next,
        "at_ceiling": is_band_ceiling(level),
        "ratio": ratio,
    }


def breakthrough_preview(
    level: int,
    gate_fails: int,
    owned_gate_qty: int,
    ho_dao_phu: bool,
    dinh_dao_chau: bool,
) -> dict:
    """Pure preview of the breakthrough at ``level`` for the UI.

    Returns ``{at_gate: False}`` when ``level`` is not a band ceiling.
    Otherwise reports the gate item requirement, whether the player holds
    enough, the success % (rounded, factoring pity + Hộ Đạo Phù), and the
    hidden flag. ``dinh_dao_chau`` doesn't move ``success_pct`` — it only
    refunds on failure — but is accepted for a symmetric call site.
    """
    if level not in GATES:
        return {"at_gate": False}

    gate = GATES[level]
    required_qty = gate["qty"]
    return {
        "at_gate": True,
        "gate_item_key": gate["item_key"],
        "required_qty": required_qty,
        "owned_qty": owned_gate_qty,
        "has_enough": owned_gate_qty >= required_qty,
        "success_pct": round(breakthrough_chance(level, gate_fails, ho_dao_phu) * 100),
        "is_hidden": gate["hidden"],
    }
