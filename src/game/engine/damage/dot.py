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

Opt-in (``dot_scales_hp_pct == True``) — rare late-game uniques:
    dmg = hp_max × base_pct

``base_pct`` for stack-based DoTs (``meta.stack_kind`` ∈ {burn / bleed /
poison}) reads ``combatant.<kind>_per_stack_pct × <kind>_stacks``; for
classic DoTs it falls back to ``meta.dot_pct``. The ``stack_kind`` field
on EffectMeta lets each effect declare its kind once in the data so the
runtime doesn't need to grow a per-effect-key branch.

After base scaling, elemental resistance and amp bonuses (``dot_dmg_bonus``
plus per-kind ``dot_dmg_bonus_by_kind[<kind>]``) apply, then an optional
crit roll (25% chance, ×1.5) when ``dot_can_crit``.

Per-effect boss caps
--------------------
Any DoT can opt into a boss-side per-tick clamp by setting a non-zero
``boss_dot_cap_matk_scale`` on its EffectMeta. When the holder carries
``is_world_boss`` or ``immune_stat_mutation``, the tick is clamped to
``scale × strongest applier matk`` (read from ``dot_bonus_sources``).
Designers tune the cap per-effect in JSON / EffectMeta — no hidden
constants. Normal mobs see no clamp. Crit roll still applies on top, so a
crit can push past the cap by ×1.5 — by design, the cap is on the base tick.
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
# When no applier power source is recorded (e.g. environment / status-only
# DoTs), fall back to a fractional hp_max tick so the DoT still deals damage.
DOT_HP_FALLBACK_MULT: float = 0.5


# Stack-DoT kinds → (stack-counter attr, per-stack-pct attr) on Combatant.
# Driven by ``EffectMeta.stack_kind`` so the data declares the kind once and
# this module doesn't need an ``if effect_key == X`` chain that grows every
# time a new stack-DoT ships.
_STACK_DOT_FIELDS: dict[str, tuple[str, str]] = {
    "burn":       ("burn_stacks",       "burn_per_stack_pct"),
    "bleed":      ("bleed_stacks",      "bleed_per_stack_pct"),
    "poison":     ("poison_stacks",     "poison_per_stack_pct"),
    "chan_hoa":   ("chan_hoa_stacks",   "chan_hoa_per_stack_pct"),
    "nghiep_hoa": ("nghiep_hoa_stacks", "nghiep_hoa_per_stack_pct"),
    "phuong_hoa": ("phuong_hoa_stacks", "phuong_hoa_per_stack_pct"),
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


def _scale_damage(
    combatant: "Combatant", base_pct: float, meta: "EffectMeta"
) -> int:
    """Scale base_pct into raw tick damage using the active DoT model.

    Caster-stat-driven DoTs (Lục Hồn Chú-class) bypass ``base_pct`` entirely
    and pull the strongest applier's recorded ``caster_hp_max`` + ``caster_matk``
    out of ``dot_bonus_sources`` — keeps the curse ticking off the caster's
    own pool + spell power regardless of what the holder looks like.
    """
    if meta.dot_caster_hp_pct > 0 or meta.dot_caster_matk_scale > 0:
        # Strongest applier wins on caster_matk (most magically potent caster
        # owns the curse). Falls back to 0 if no source was recorded — the
        # curse still applies but its tick collapses to a min-1 floor.
        src = max(
            combatant.dot_bonus_sources.values(),
            key=lambda s: s.get("caster_matk", 0),
            default=None,
        )
        hp_max = int((src or {}).get("caster_hp_max", 0))
        matk = int((src or {}).get("caster_matk", 0))
        return max(
            1,
            int(hp_max * meta.dot_caster_hp_pct)
            + int(matk * meta.dot_caster_matk_scale),
        )
    # Target-HP-driven DoT — flat % of the holder's hp_max every tick,
    # independent of attacker stats / stack counters. Read at tick time so
    # changes to ``hp_max`` mid-fight (Tận Diệt etc.) flow through naturally.
    if meta.dot_target_hp_pct > 0:
        return max(1, int(combatant.hp_max * meta.dot_target_hp_pct))
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
    """Reduce by the holder's resistance to the DoT's element, capped by the
    holder's per-element cap (player soft cap + bonus, or hard cap for enemies)."""
    if not meta.dot_element:
        return dmg
    from src.game.engine.effects import effective_res_cap
    cap = effective_res_cap(combatant, meta.dot_element)
    res_pct = max(0.0, min(cap, combatant.resistances.get(meta.dot_element, 0.0)))
    return max(1, int(dmg * (1.0 - res_pct)))


def _dot_amp(combatant: "Combatant", meta: "EffectMeta") -> float:
    """Sum the global + per-kind DoT amp bonuses for this effect.

    Per-kind amps live in ``combatant.dot_dmg_bonus_by_kind`` keyed by
    stack_kind (``burn`` / ``bleed`` / ``poison``). Chân Hỏa has no dedicated
    entry — its stacks instead boost *every* hoa-element DoT via the
    dot_element gate below.

    Includes ``dot_taken_bonus`` aggregated from the holder's active effects
    (lazy import to avoid the dot↔effects cycle) — lets a debuff like Lục
    Hồn Chú stamp ``stat_bonus={"dot_taken_bonus": 0.20}`` and have every
    DoT tick on the holder amplify by that much without needing a dedicated
    Combatant field.

    Tam Muội Chân Hỏa: each stack adds ``chan_hoa_per_stack_fire_amp`` to
    every hoa-element DoT ticking on the holder. Skill stacks scale all
    fire DoTs (burn / blaze / Chân Hỏa itself) without needing a dedicated
    per-DoT lookup table — the gate is purely on ``meta.dot_element``.
    """
    amp = combatant.dot_dmg_bonus
    if meta.stack_kind:
        amp += float(combatant.dot_dmg_bonus_by_kind.get(meta.stack_kind, 0.0))
    if meta.dot_element == "hoa":
        ch_stacks = int(getattr(combatant, "chan_hoa_stacks", 0))
        per_stack = float(getattr(combatant, "chan_hoa_per_stack_fire_amp", 0.0))
        if ch_stacks > 0 and per_stack > 0:
            amp += ch_stacks * per_stack
    from src.game.engine.effects import get_combat_modifiers
    amp += float(get_combat_modifiers(combatant).get("dot_taken_bonus", 0.0))
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
    dmg = _scale_damage(combatant, _base_pct(combatant, effect_key, meta), meta)
    dmg = _apply_resistance(dmg, combatant, meta)

    amp = _dot_amp(combatant, meta)
    if amp > 0:
        dmg = max(1, int(dmg * (1.0 + amp)))

    # Per-effect boss cap — any DoT can opt into this by setting a non-zero
    # ``boss_dot_cap_matk_scale`` on its EffectMeta. Without it, late-game
    # stacking can nuke world-boss HP pools regardless of how the formula
    # was tuned for normal mobs. Cap formula: ``scale × strongest applier
    # matk`` (read from ``dot_bonus_sources``). Applies only when the holder
    # carries ``is_world_boss`` or ``immune_stat_mutation`` — normal mobs
    # see no clamp. Set the scale on EffectMeta where designers see it,
    # not as a hidden constant.
    cap_scale = float(getattr(meta, "boss_dot_cap_matk_scale", 0.0))
    if cap_scale > 0 and (
        getattr(combatant, "is_world_boss", False)
        or getattr(combatant, "immune_stat_mutation", False)
    ):
        max_caster_matk = max(
            (s.get("caster_matk", 0) for s in combatant.dot_bonus_sources.values()),
            default=0,
        )
        if max_caster_matk > 0:
            cap = max(1, int(max_caster_matk * cap_scale))
            if dmg > cap:
                dmg = cap

    is_crit = False
    if combatant.dot_can_crit and rng.random() < DOT_CRIT_CHANCE:
        dmg = int(dmg * DOT_CRIT_MULT)
        is_crit = True
    return dmg, is_crit
