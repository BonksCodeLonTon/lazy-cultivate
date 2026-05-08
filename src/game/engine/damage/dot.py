"""DoT (damage-over-time) tick calculation.

One pure function per DoT tick; ``get_periodic_damage`` in ``engine/effects.py``
loops active effects and delegates the math here so all damage formulas live
under ``engine/damage``.

Tick model
----------
Default (``dot_scales_hp_pct == False``):
    dmg = max_applier_power × base_pct × DOT_POWER_COEF
    where ``max_applier_power`` = max(atk, matk) at DoT-apply time, stored in
    ``combatant.dot_bonus_sources[applier_key]["power"]``. A missing source
    (e.g. enemy-applied DoT before source tracking) falls back to
    ``hp_max × base_pct × 0.5``.

Legacy (``dot_scales_hp_pct == True``) — rare late-game uniques:
    dmg = hp_max × base_pct

``base_pct`` for stack-based DoTs (``meta.stack_kind`` ∈ {burn / bleed /
poison}) reads ``combatant.<kind>_per_stack_pct × <kind>_stacks``; for
classic DoTs it falls back to ``meta.dot_pct``. The ``stack_kind`` field
on EffectMeta lets each effect declare its kind once in the data so the
runtime doesn't need to grow a per-effect-key branch.

After base scaling, elemental resistance and amp bonuses (``dot_dmg_bonus``
plus per-type ``burn_dmg_bonus`` / ``bleed_dmg_bonus`` / ``poison_dmg_bonus``)
apply, then an optional crit roll (25% chance, ×1.5) when ``dot_can_crit``.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from src.game.constants.balance import DOT_POWER_COEF, MAX_ELEMENTAL_RES

if TYPE_CHECKING:
    from src.game.engine.effects import EffectMeta
    from src.game.systems.combatant import Combatant


DOT_CRIT_CHANCE: float = 0.25
DOT_CRIT_MULT:   float = 1.5
# When no applier power source is recorded, fall back to a fractional hp_max
# tick so the DoT still deals damage (prevents 0 damage for legacy enemy DoTs).
DOT_HP_FALLBACK_MULT: float = 0.5


# Stack-DoT kinds → (stack-counter attr, per-stack-pct attr) on Combatant.
# Driven by ``EffectMeta.stack_kind`` so the data declares the kind once and
# this module doesn't need an ``if effect_key == X`` chain that grows every
# time a new stack-DoT ships.
_STACK_DOT_FIELDS: dict[str, tuple[str, str]] = {
    "burn":   ("burn_stacks",   "burn_per_stack_pct"),
    "bleed":  ("bleed_stacks",  "bleed_per_stack_pct"),
    "poison": ("poison_stacks", "poison_per_stack_pct"),
}


def _base_pct(combatant: "Combatant", effect_key: str, meta: "EffectMeta") -> float:
    """Return the DoT's base tick fraction before scaling/resistance/amp.

    Stack DoTs (``meta.stack_kind`` set) tick from the combatant's stack
    counter × per-stack pct. Classic DoTs use the static ``meta.dot_pct``.
    The clamp ``max(1, stacks)`` keeps the first tick working when the
    effect is applied before any stack has been added.
    """
    fields = _STACK_DOT_FIELDS.get(meta.stack_kind) if meta.stack_kind else None
    if fields:
        stacks_attr, pct_attr = fields
        stacks = max(1, getattr(combatant, stacks_attr, 0))
        return float(getattr(combatant, pct_attr, 0.0)) * stacks
    return meta.dot_pct


def _scale_damage(combatant: "Combatant", base_pct: float) -> int:
    """Scale base_pct into raw tick damage using the active DoT model."""
    if combatant.dot_scales_hp_pct:
        return max(1, int(combatant.hp_max * base_pct))
    max_power = max(
        (s.get("power", 0) for s in combatant.dot_bonus_sources.values()),
        default=0,
    )
    if max_power <= 0:
        return max(1, int(combatant.hp_max * base_pct * DOT_HP_FALLBACK_MULT))
    return max(1, int(max_power * base_pct * DOT_POWER_COEF))


def _apply_resistance(dmg: int, combatant: "Combatant", meta: "EffectMeta") -> int:
    """Reduce by the holder's resistance to the DoT's element, capped at MAX_ELEMENTAL_RES."""
    if not meta.dot_element:
        return dmg
    res_pct = max(0.0, min(MAX_ELEMENTAL_RES, combatant.resistances.get(meta.dot_element, 0.0)))
    return max(1, int(dmg * (1.0 - res_pct)))


# Stack-DoT kind → per-type damage-amp attribute on Combatant. Mirrors
# ``_STACK_DOT_FIELDS`` so the per-kind amp lookup is data-driven too.
_STACK_DOT_AMP_ATTR: dict[str, str] = {
    "burn":   "burn_dmg_bonus",
    "bleed":  "bleed_dmg_bonus",
    "poison": "poison_dmg_bonus",
}


def _dot_amp(combatant: "Combatant", meta: "EffectMeta") -> float:
    """Sum the global + per-type DoT amp bonuses for this effect."""
    amp = combatant.dot_dmg_bonus
    attr = _STACK_DOT_AMP_ATTR.get(meta.stack_kind) if meta.stack_kind else None
    if attr:
        amp += float(getattr(combatant, attr, 0.0))
    return amp


def calculate_dot_damage(
    combatant: "Combatant",
    effect_key: str,
    meta: "EffectMeta",
    rng: random.Random,
) -> tuple[int, bool]:
    """Compute one DoT tick on ``combatant``.

    Returns (damage, is_crit). Caller is responsible for filtering out
    effects with ``dot_pct <= 0`` and for honoring poison_immunity before
    calling — those policy checks stay in ``get_periodic_damage``.
    """
    dmg = _scale_damage(combatant, _base_pct(combatant, effect_key, meta))
    dmg = _apply_resistance(dmg, combatant, meta)

    amp = _dot_amp(combatant, meta)
    if amp > 0:
        dmg = max(1, int(dmg * (1.0 + amp)))

    is_crit = False
    if combatant.dot_can_crit and rng.random() < DOT_CRIT_CHANCE:
        dmg = int(dmg * DOT_CRIT_MULT)
        is_crit = True
    return dmg, is_crit
