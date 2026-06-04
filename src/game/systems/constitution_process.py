"""Constitution Process (Tiến Trình Thể Chất) pure logic — Phase 1 core.

Deterministic, side-effect-free functions over the tables in
``src.game.constants.constitution_process``. RNG is always injected by the
caller (``roll`` arguments) so these functions stay testable and
reproducible — this module never imports ``random``.

The composition rule (``effective_stat_bonuses``) layers a constitution's
optional ``process`` block on top of its flat ``stat_bonuses`` using the same
additive-merge semantics the constitution reader already uses
(``cultivation._merge_bonus_dict``), so a process-aware body stacks exactly
the way a plain one does. Bodies WITHOUT a ``process`` block return their flat
``stat_bonuses`` unchanged — this is what keeps every existing constitution
byte-identical while the feature is dormant.
"""
from __future__ import annotations

import copy

from src.game.constants.constitution_process import (
    BAND_LABELS,
    CEILINGS,
    DINH_THE_CHAU_KEY,
    GATES,
    HO_THE_PHU_BONUS,
    HO_THE_PHU_KEY,
    MAX_LEVEL,
    MILESTONES,
    XP_GRADE_SCALE,
    XP_PER_COMBAT,
    XP_TO_NEXT,
    XP_WIN_BONUS,
    band_of,
)
from src.game.systems.cultivation import _merge_bonus_dict
from src.game.systems.the_chat import (
    HON_DON_KEY,
    get_constitutions,
    get_tracker,
    set_constitutions,
)


def effective_stat_bonuses(const_data: dict, level: int) -> dict:
    """Compose a constitution's flat ``stat_bonuses`` with its process block.

    1. base = copy of ``const_data["stat_bonuses"]`` (or ``{}``).
    2. No ``process`` block → return ``base`` unchanged (backward-compat — this
       is what keeps every existing constitution identical).
    3. For each milestone ``m`` in ``process["milestones"]`` with ``m <= level``,
       additively merge ``process["levels"][str(m)]["stat_bonuses"]`` using the
       same nested-dict / scalar-add / bool-last-write semantics as the
       constitution reader (``_merge_bonus_dict``).
    4. If ``process["per_level_growth"]`` is present, add ``value * (level - 1)``
       to each numeric stat it names — growth is 0 at level 1 so a fresh body
       equals its flat base (the "L1 == flat" identity), reaching ``value * 8``
       at L9.
    """
    process = const_data.get("process")
    if not process:
        # No process layer: return the flat bonuses verbatim. A shallow copy is
        # enough here — nothing mutates it, and matching the reader's plain
        # ``c.get("stat_bonuses")`` read keeps inert bodies byte-identical.
        return dict(const_data.get("stat_bonuses") or {})

    # WHY deepcopy: ``_merge_bonus_dict`` mutates nested dicts in place via
    # ``setdefault``. A shallow copy would share ``const_data``'s nested dicts
    # (e.g. ``dot_dmg_bonus_by_kind``) so merging milestones would corrupt the
    # source data and leak across calls. Deep-copy the base so composition is
    # pure.
    base = copy.deepcopy(const_data.get("stat_bonuses") or {})

    for m in process.get("milestones", []):
        if m <= level:
            level_block = process.get("levels", {}).get(str(m), {})
            _merge_bonus_dict(base, level_block.get("stat_bonuses", {}))

    growth = process.get("per_level_growth")
    if growth:
        # WHY (level - 1): growth must be 0 at L1 so an untrained body equals its
        # flat base — the "L1 == flat" identity the combat-read tests pin. At L9
        # the growth term is value * 8.
        for stat, val in growth.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                add = val * (level - 1)
                base[stat] = base.get(stat, type(val)(0)) + (
                    type(val)(add) if isinstance(val, int) else add
                )

    return base


def effective_effects(const_data: dict, level: int) -> list[str]:
    """Dedup union of milestone (and flat-level) effects for milestones <= level.

    Preserves first-seen order. Pulls ``effects`` from each milestone block in
    the process tree plus any flat top-level ``effects`` on the milestone (none
    exist in data today, but the shape is supported).
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(effects) -> None:
        for e in effects or []:
            if e not in seen:
                seen.add(e)
                out.append(e)

    process = const_data.get("process") or {}
    for m in process.get("milestones", []):
        if m <= level:
            level_block = process.get("levels", {}).get(str(m), {})
            _add(level_block.get("effects"))

    return out


def xp_to_next(level: int) -> int | None:
    """XP needed to advance from ``level``; None at ceilings 2/5/8 and MAX 9."""
    return XP_TO_NEXT.get(level)


def is_ceiling(level: int) -> bool:
    """True if ``level`` is a gated ceiling (2, 5, 8)."""
    return level in CEILINGS


def band_label(level: int) -> tuple[str, str]:
    """(vietnamese, english) label for the milestone band containing ``level``."""
    return BAND_LABELS[band_of(level)]


def apply_combat_xp(level: int, xp: int, gained: int) -> tuple[int, int, bool]:
    """Add combat ``gained`` XP and roll up levels within the current band.

    Stops at a ceiling (2/5/8) or MAX_LEVEL. WHY: ceilings are trial+material
    gated breakthroughs — XP alone cannot cross them. On reaching a stop,
    surplus XP is discarded (locked decision): returns the stop level with
    ``xp=0`` and ``at_ceiling=True``. Within a band, leftover XP carries into
    the next level normally.

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


def combat_xp_gain(grade: str, won: bool) -> int:
    """XP a constitution earns from one completed PvE combat.

    ``round((XP_PER_COMBAT + win_bonus) * grade_scale)``. Unknown grades and
    ``trash`` scale to 0.
    """
    flat = XP_PER_COMBAT + (XP_WIN_BONUS if won else 0)
    return round(flat * XP_GRADE_SCALE.get(grade, 0.0))


def breakthrough_chance(gate_level: int, fails: int, ho_the_phu: bool) -> float:
    """Success probability for the breakthrough at ``gate_level``.

    ``base_success + fails*pity_per_fail + Hộ Thể Phù bonus``, clamped to
    [0.0, 1.0]. ``gate_level`` must be a key in GATES.
    """
    gate = GATES[gate_level]
    chance = gate["base_success"] + fails * gate["pity_per_fail"]
    if ho_the_phu:
        chance += HO_THE_PHU_BONUS
    return max(0.0, min(1.0, chance))


def resolve_breakthrough(
    gate_level: int,
    fails: int,
    ho_the_phu: bool,
    dinh_the_chau: bool,
    roll: float,
) -> dict:
    """Resolve one breakthrough attempt at ``gate_level`` against ``roll``.

    Caller supplies ``roll`` in [0, 1); success iff ``roll < chance``.

    - Success: level becomes ``gate_level + 1``, gate stack consumed, fails reset.
    - Fail: level unchanged, fails incremented, gate stack consumed UNLESS
      Định Thể Châu refunds it.
    - Hộ Thể Phù is consumed on every attempt; Định Thể Châu only on a fail.
    """
    gate = GATES[gate_level]
    qty = gate["qty"]
    chance = breakthrough_chance(gate_level, fails, ho_the_phu)
    success = roll < chance

    if success:
        return {
            "success": True,
            "new_level": gate_level + 1,
            "new_fails": 0,
            "consumed_gate_qty": qty,
            "refunded": False,
            "consumed_ho_the_phu": ho_the_phu,
            "consumed_dinh_the_chau": False,
        }

    refunded = dinh_the_chau
    return {
        "success": False,
        "new_level": gate_level,
        "new_fails": fails + 1,
        "consumed_gate_qty": 0 if refunded else qty,
        "refunded": refunded,
        "consumed_ho_the_phu": ho_the_phu,
        "consumed_dinh_the_chau": dinh_the_chau,
    }


def progress_summary(level: int, xp: int) -> dict:
    """Pure UI snapshot of a single constitution's progression.

    ``ratio`` is the bar fill in [0, 1]; at a ceiling/MAX (``xp_next is None``)
    it reads full.
    """
    vi, en = band_label(level)
    xp_next = xp_to_next(level)
    if xp_next is None:
        ratio = 1.0
    else:
        ratio = max(0.0, min(1.0, xp / xp_next))
    return {
        "band_vi": vi,
        "band_en": en,
        "level": level,
        "cap": MAX_LEVEL,
        "xp": xp,
        "xp_next": xp_next,
        "at_ceiling": is_ceiling(level),
        "ratio": ratio,
    }


def breakthrough_preview(
    level: int,
    gate_fails: int,
    owned_gate_qty: int,
    ho_the_phu: bool,
    dinh_the_chau: bool,
) -> dict:
    """Pure preview of the breakthrough at ``level`` for the UI.

    Returns ``{at_gate: False}`` when ``level`` is not a ceiling. Otherwise
    reports the gate item requirement, whether the player holds enough, the
    success % (rounded, factoring pity + Hộ Thể Phù), and the trial tier.
    ``dinh_the_chau`` doesn't move ``success_pct`` — it only refunds on
    failure — but is accepted for a symmetric call site.
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
        "success_pct": round(breakthrough_chance(level, gate_fails, ho_the_phu) * 100),
        "trial_tier": gate["trial_tier"],
    }


def resolve_constitution_breakthrough(
    level: int,
    gate_fails: int,
    owned_gate_qty: int,
    use_ho_the_phu: bool,
    use_dinh_the_chau: bool,
    trial_won: bool,
    roll: float,
) -> dict:
    """Resolve one full Constitution Process breakthrough attempt.

    PURE — RNG is injected via ``roll`` (in ``[0, 1)``; success iff
    ``roll < chance``). This is the Phase-5 orchestration layer the cog calls
    AFTER the trial fight resolves; it owns the consumption table but never
    touches inventory/DB (the caller spends the returned ``consumed`` map).

    Flow:
      0. ``level`` not a gate ceiling → ``{outcome: "NOT_AT_GATE"}``, no spend.
      1. ``trial_won`` is False → ``{outcome: "TRIAL_LOST"}``, no spend
         (the trial is the entry toll; losing it forfeits nothing).
      2. ``owned_gate_qty`` below the gate's required ``qty`` →
         ``{outcome: "NOT_ENOUGH_MATERIALS"}``, no spend.
      3. ``chance = breakthrough_chance(level, gate_fails, use_ho_the_phu)``.
      4. ``success = roll < chance``.
      5. Resolve + consumption:
         - SUCCESS: gate mats consumed, level +1, fails reset to 0.
         - FAIL no-Định: gate mats burned, level unchanged, fails +1.
         - FAIL +Định: gate mats refunded, Định Thể Châu consumed, fails +1.
         - Hộ Thể Phù: consumed on EVERY reach-the-roll attempt (success or fail).
         - Định Thể Châu: consumed ONLY on a fail.
      No realm/level loss ever occurs on failure (locked decision).

    Returns ``{outcome, new_level, new_xp, new_fails, consumed, refunded_gate_mats,
    chance_used}`` on SUCCESS/FAIL. The ``consumed`` map is keyed by the gate
    item plus the two optional aids (``0`` qty when not consumed).
    """
    if level not in GATES:
        return {"outcome": "NOT_AT_GATE"}

    # Losing the trial forfeits nothing — the trial is the gate to even attempt
    # the breakthrough, so a loss returns the player to where they were.
    if not trial_won:
        return {"outcome": "TRIAL_LOST"}

    gate = GATES[level]
    gate_item = gate["item_key"]
    required_qty = gate["qty"]

    if owned_gate_qty < required_qty:
        return {"outcome": "NOT_ENOUGH_MATERIALS"}

    chance = breakthrough_chance(level, gate_fails, use_ho_the_phu)
    success = roll < chance

    if success:
        return {
            "outcome": "SUCCESS",
            "new_level": level + 1,
            "new_xp": 0,
            "new_fails": 0,
            "consumed": {
                gate_item: required_qty,
                HO_THE_PHU_KEY: 1 if use_ho_the_phu else 0,
                DINH_THE_CHAU_KEY: 0,
            },
            "refunded_gate_mats": False,
            "chance_used": chance,
        }

    # FAIL — gate mats are refunded iff Định Thể Châu is in play (and the pearl
    # itself is then consumed). No level/realm loss on failure.
    refunded = use_dinh_the_chau
    return {
        "outcome": "FAIL",
        "new_level": level,
        "new_xp": 0,
        "new_fails": gate_fails + 1,
        "consumed": {
            gate_item: 0 if refunded else required_qty,
            HO_THE_PHU_KEY: 1 if use_ho_the_phu else 0,
            DINH_THE_CHAU_KEY: 1 if use_dinh_the_chau else 0,
        },
        "refunded_gate_mats": refunded,
        "chance_used": chance,
    }


def resolve_constitution_swap(
    constitution_type: str | None,
    constitution_tracker: str | None,
    target_key: str,
    owned_essence_qty: int,
) -> dict:
    """Resolve a Hoán Thể Tinh body-swap — change the PRIMARY constitution.

    PURE, no RNG. Moves ``target_key`` to the front of the equipped STANDARD
    list (slot 0 = the XP-earning primary), keeping Hỗn Độn pinned last when
    present. Progress rows are NEVER touched here — the swap-retains-progress
    invariant lives in the per-``(player, constitution)`` storage, so the new
    primary simply reads its existing level (or defaults to L1).

    Guards (all reject with zero consumption):
      - ``target_key`` not currently equipped → ``NOT_EQUIPPED``
      - ``target_key`` not in the unlock tracker → ``NOT_UNLOCKED``
      - ``target_key`` already slot 0 → ``ALREADY_PRIMARY``
      - ``target_key`` is Hỗn Độn (special 9th slot) → ``INVALID_TARGET``
      - ``owned_essence_qty < 1`` → ``NO_ESSENCE``

    On success returns ``{outcome: "SWAPPED", new_constitution_type, consumed}``
    where ``new_constitution_type`` is the re-serialized equipped column.
    """
    equipped = get_constitutions(constitution_type)
    tracker = get_tracker(constitution_tracker)

    if target_key == HON_DON_KEY:
        return {"outcome": "INVALID_TARGET"}
    if target_key not in equipped:
        return {"outcome": "NOT_EQUIPPED"}
    if target_key not in tracker:
        return {"outcome": "NOT_UNLOCKED"}

    # Hỗn Độn is the special 9th slot — never a "primary"; reorder only the
    # standard list and re-pin Hỗn Độn last so its slot identity is preserved.
    has_hon_don = HON_DON_KEY in equipped
    standard = [k for k in equipped if k != HON_DON_KEY]

    if standard and standard[0] == target_key:
        return {"outcome": "ALREADY_PRIMARY"}

    if owned_essence_qty < 1:
        return {"outcome": "NO_ESSENCE"}

    reordered = [target_key] + [k for k in standard if k != target_key]
    if has_hon_don:
        reordered.append(HON_DON_KEY)

    return {
        "outcome": "SWAPPED",
        "new_constitution_type": set_constitutions(reordered),
        "consumed": {"ConsProcHoanTheTinh": 1},
    }
