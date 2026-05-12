"""Generic stat-difference bonus folding.

Pattern: when an active effect carries a stat_bonus key shaped like
``<output_stat>_per_<source_stat>_diff: <magnitude>``, the helper here
computes ``gap = max(0, beneficiary_source - opponent_source)`` and folds
``gap * magnitude`` into ``beneficiary_mods[output_stat]``. The receiving
engine consumer reads the standard stat key transparently — no special
casing per buff.

Output names must match the *exact* stat key the engine reads — e.g.
``evasion_rating`` (not ``evasion``), ``final_dmg_reduce``, ``crit_rating``,
``dmg_bonus_<elem>``, ``spd_pct``, etc. Rule of thumb: the same key any
other buff would put in its plain ``stat_bonus``. Examples (all driven by
the same helper):

  • ``evasion_rating_per_spd_diff: 20.0``    — Hải Thị Thận Lâu's +20
                                                evasion per 1 spd above
                                                the attacker
  • ``final_dmg_reduce_per_def_diff: 0.001`` — "tankier than them ⇒ less
                                                damage taken"
  • ``dmg_bonus_thuy_per_atk_diff: 0.0005``  — "stronger than them ⇒ amped
                                                outgoing thuy damage"
  • ``crit_rating_per_matk_diff: 0.5``       — "magic-stronger than them ⇒
                                                higher crit chance"

A second key family handles flat-magnitude binary gates rather than
gap-scaled bonuses:

  • ``<output>_if_<source>_higher: <flat>`` — fires once when the
                                              beneficiary's source stat is
                                              strictly greater than the
                                              opponent's
  • ``<output>_if_<source>_lower:  <flat>`` — comeback-flavor mirror; fires
                                              when the beneficiary's source
                                              stat is strictly less than the
                                              opponent's

Strict comparison ⇒ exact ties grant neither branch, mirroring
Tứ Lạng's HP-gate semantics. Magnitudes are floats; integer outputs (e.g.
``evasion_rating``) get truncated by the int() rounding at fold time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


# ── Source-stat registry ─────────────────────────────────────────────────────
# Maps the ``<source_stat>`` token in the diff key to a function that pulls the
# combatant's effective value. ``mods`` is the active-effect mod dict for that
# combatant — used so spd folds in spd_pct buffs (Hải Thị active counterpart
# amplifies the passive evasion gain). Other stats currently use raw fields
# because no standard ``<stat>_pct`` buff path exists for them yet — extend
# the lambdas if/when that changes.
_STAT_SOURCES: dict[str, Callable[["Combatant", dict], float]] = {
    "spd":     lambda c, m: max(1, c.spd * (1.0 + float(m.get("spd_pct", 0.0)))),
    "atk":     lambda c, m: float(c.atk),
    "matk":    lambda c, m: float(c.matk),
    "def":     lambda c, m: float(c.def_stat),
    "hp_max":  lambda c, m: float(c.hp_max),
    "mp_max":  lambda c, m: float(c.mp_max),
}


_DIFF_SEP = "_per_"
_DIFF_SUFFIX = "_diff"

# Binary comparison gates — flat magnitudes that fire iff the beneficiary's
# source stat is strictly higher / lower than the opponent's. Used by buffs
# that flip behavior based on "who's faster" rather than scaling per-point.
# Strict comparison ⇒ exact ties grant neither branch (mirrors Tứ Lạng's
# HP-gate semantics).
_IF_SEP = "_if_"
_IF_HIGHER_SUFFIX = "_higher"
_IF_LOWER_SUFFIX = "_lower"


def apply_stat_diff_bonuses(
    beneficiary: "Combatant", beneficiary_mods: dict,
    opponent: "Combatant", opponent_mods: dict,
) -> None:
    """Scan ``beneficiary_mods`` for diff-style keys and fold the computed
    bonus into ``beneficiary_mods[output]`` in place. Two key families:

      • ``<output>_per_<source>_diff`` — gap-scaled. Fires when beneficiary's
        source stat exceeds the opponent's; bonus = gap × magnitude.
      • ``<output>_if_<source>_higher`` / ``<output>_if_<source>_lower`` —
        flat magnitude. Fires once when the strict comparison holds.

    The mod dict is mutated rather than replaced so downstream readers
    (build_attack_stats / build_defense_stats) see the bonus through the
    standard stat lookup. Unknown source tokens are skipped silently —
    keeps stale or typoed keys from raising. Negative magnitudes work for
    both families (a "you're faster ⇒ you take MORE damage" debuff would
    set ``final_dmg_taken_bonus_per_spd_diff: 0.005`` on the slower side,
    or use the ``_if_*_lower`` family for a flat penalty).
    """
    for key, magnitude in list(beneficiary_mods.items()):
        try:
            mag = float(magnitude)
        except (TypeError, ValueError):
            continue
        if mag == 0:
            continue

        if key.endswith(_DIFF_SUFFIX) and _DIFF_SEP in key:
            output, rest = key.rsplit(_DIFF_SEP, 1)
            source = rest[: -len(_DIFF_SUFFIX)]
            accessor = _STAT_SOURCES.get(source)
            if accessor is None:
                continue
            gap = max(0.0, accessor(beneficiary, beneficiary_mods) - accessor(opponent, opponent_mods))
            if gap <= 0:
                continue
            bonus = gap * mag
        elif _IF_SEP in key and (
            key.endswith(_IF_HIGHER_SUFFIX) or key.endswith(_IF_LOWER_SUFFIX)
        ):
            higher_branch = key.endswith(_IF_HIGHER_SUFFIX)
            suffix_len = len(_IF_HIGHER_SUFFIX if higher_branch else _IF_LOWER_SUFFIX)
            output, rest = key.rsplit(_IF_SEP, 1)
            source = rest[: -suffix_len]
            accessor = _STAT_SOURCES.get(source)
            if accessor is None:
                continue
            self_val = accessor(beneficiary, beneficiary_mods)
            opp_val = accessor(opponent, opponent_mods)
            fires = self_val > opp_val if higher_branch else self_val < opp_val
            if not fires:
                continue
            bonus = mag
        else:
            continue

        # int-output stats round; float-output stats keep the precision.
        if output in _INTEGER_OUTPUTS:
            bonus = int(bonus)
        beneficiary_mods[output] = beneficiary_mods.get(output, 0.0) + bonus


# Outputs that the engine reads as integers — bonus is truncated to int when
# folded. Anything else (final_dmg_reduce, dmg_bonus_*, *_pct) stays float.
_INTEGER_OUTPUTS: frozenset[str] = frozenset({
    "evasion_rating",
    "crit_rating",
    "crit_dmg_rating",
    "crit_res_rating",
    "accuracy_rating",
})
