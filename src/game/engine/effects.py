"""Combat effects registry — definitions loaded from src/data/effects/*.json.

Effect *data* (buffs / debuffs / CC) lives in JSON, mirroring the skill
data pattern; this module owns only the ``EffectMeta`` shape and the
behaviour helpers that interpret it.

Provides:
  EFFECTS                 dict[key → EffectMeta]
  get_combat_modifiers    sum all active effect stat modifiers on a combatant
  get_periodic_damage     list of (effect_key, damage) for active DoTs
  check_cc_skip_turn      returns CC key if combatant should skip turn, else None
  check_prevents_skills   returns CC key if combatant cannot use skills, else None
  default_duration        default turn duration for an effect
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from src.game.constants.effects import EffectKey


if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


class EffectKind(StrEnum):
    BUFF = "buff"
    DEBUFF = "debuff"
    CC = "cc"


@dataclass(frozen=True)
class EffectMeta:
    key: str
    vi: str                          # Vietnamese name
    en: str                          # English name
    kind: EffectKind
    description_vi: str
    # Stat modifiers while effect is active (see stat key docs below)
    # Positive = bonus, negative = penalty. Applied to the combatant who holds the effect.
    # Stat keys: final_dmg_bonus, final_dmg_reduce, crit_rating, crit_dmg_rating,
    #            evasion_rating, crit_res_rating, spd_pct, hp_regen_pct, res_all
    stat_bonus: dict[str, float] = field(default_factory=dict)
    # Periodic damage per turn as fraction of holder's hp_max (DoT effects).
    # Stack-based DoTs (burn / bleed / poison) leave this at 0.0 and declare
    # ``stack_kind`` instead — the per-tick damage there comes from
    # ``combatant.<kind>_per_stack_pct × stacks`` (see engine/damage/dot.py).
    dot_pct: float = 0.0
    # Element of the DoT damage — holder's resistance to this element reduces DoT damage
    dot_element: str | None = None
    # Stack-based DoT kind ("burn" / "bleed" / "poison"). When set, the
    # tick math reads the combatant's per-stack pct and stack counter
    # instead of ``dot_pct``. Lets each stack DoT declare its kind in the
    # data once and removes the per-effect-key branches from dot.py.
    stack_kind: str | None = None
    # Whether this CC effect causes the holder to skip their turn (deterministic)
    skips_turn: bool = False
    # Probabilistic sibling of ``skips_turn``: per-turn chance (0.0–1.0) the
    # holder skips their turn while this effect is active. Rolled in
    # ``check_cc_skip_turn`` after the deterministic ``skips_turn`` check.
    # Replaces the old per-effect-key hardcodes (paralysis 0.50 / fear 0.20)
    # — any effect can now declare its own skip chance in the JSON data.
    skip_turn_chance: float = 0.0
    # Per-swing chance (0.0–1.0) the holder's attack whiffs while this effect
    # is active (blind-style). Rolled in ``check_attack_miss`` — the holder
    # still pays MP/CD, the swing just fails to land. Replaces the old
    # DebuffLoaMat ``BLIND_MISS_CHANCE`` hardcode.
    miss_chance: float = 0.0
    # Whether this effect prevents skill usage (silence / interrupt)
    prevents_skills: bool = False
    # Aura-on-hit: ``(effect_key, chance)`` or ``(effect_key, chance, duration)``
    # — when the holder lands a hit, roll ``chance`` to apply ``effect_key``
    # to the target. Used for buffs whose flavor affects enemies (e.g.
    # BuffHanKhi slows anyone the holder strikes). The optional 3rd element
    # overrides the default turn duration for the spread effect — without it
    # the proc falls back to ``default_duration(effect_key)``. Applied in
    # ``combat._run_on_hit_procs``; respects hard-CC immunity.
    aura_on_hit: tuple[str, float] | tuple[str, float, int] | None = None
    # Instant-pulse magnitudes (one-shot when the effect is "applied" by a
    # support skill, NOT a per-turn DoT/regen). Used by HpRegen / MpRegen
    # and any future cleanse/recharge effects. A skill can override on a
    # per-cast basis via ``effect_overrides[<key>]``:
    #   "effect_overrides": { "HpRegen": { "instant_heal_pct": 0.25 } }
    # Routed through ``_apply_heal`` so bleed-heal-reduce, heal-can-crit,
    # and queued_heal_dmg conversions all behave consistently.
    instant_heal_pct: float = 0.0
    instant_mp_pct:   float = 0.0
    # On each DoT tick, ``dot_shield_drain_pct`` × tick damage also drains the
    # holder's energy shield (regular DoT bypasses shield). Used by curses that
    # punch through PoE-style shields. ``dot_mp_drain_pct`` × the holder's
    # ``mp_max`` is taken from MP per tick. Both default to zero — only set on
    # effects that need anti-shield / mana-burn flavor (e.g. Lục Hồn Chú).
    dot_shield_drain_pct: float = 0.0
    dot_mp_drain_pct:     float = 0.0
    # On each DoT tick, ``dot_applier_heal_pct`` × the (post-boss-cap) tick
    # damage is healed back to the STRONGEST applier of this DoT (read from the
    # holder's ``dot_bonus_sources`` — the same map the caster-scaling tick
    # uses). Lets a soul-eating curse like Thực Hồn drain HP from the target
    # straight into its caster. Default 0.0 → every existing DoT byte-identical
    # (no heal, no extra RNG). Routed through ``session._apply_heal`` so
    # bleed-heal-reduce / heal-can-crit behave consistently.
    dot_applier_heal_pct: float = 0.0
    # Caster-stat-driven DoT tick. When EITHER field is > 0, the standard
    # ``power × dot_pct × DOT_POWER_COEF`` formula is bypassed for this effect
    # and the tick instead reads the strongest applier's recorded stats:
    #     tick = caster.hp_max × dot_caster_hp_pct
    #          + caster.matk   × dot_caster_matk_scale
    # Lets a curse like Lục Hồn Chú scale off the caster's own HP pool + spell
    # power instead of the holder's stats. ``dot_pct`` becomes irrelevant when
    # these are set; resistance + ``dot_taken_bonus`` amps still apply on top.
    dot_caster_hp_pct:     float = 0.0
    dot_caster_matk_scale: float = 0.0
    # Target-HP-driven DoT tick. When > 0, the tick is computed as
    # ``holder.hp_max × dot_target_hp_pct`` — independent of attacker stats,
    # stack counters, or applier power sources. Resistance + ``dot_taken_bonus``
    # amps still apply on top. Used for "X% target HP per turn" effects like
    # Phệ Huyết Thực where the design wants a flat percentage regardless of
    # build (no scaling variance). Mutually exclusive with caster-stat scaling.
    dot_target_hp_pct: float = 0.0
    # Per-tick damage cap for this DoT against world-boss / stat-mutation-immune
    # holders, expressed as a multiplier of the strongest applier's recorded
    # ``caster_matk``. 0.0 (the default) means uncapped — most DoTs don't need
    # one because their tick formula already self-limits via per-stack pct.
    # Set this when a DoT's stack scaling can otherwise nuke huge boss HP pools
    # (e.g. ``DebuffChanHoa: 6.0`` → tick clamped to 6×matk on world bosses).
    # Bosses without ``is_world_boss`` / ``immune_stat_mutation`` see no cap.
    boss_dot_cap_matk_scale: float = 0.0
    # Stack-DoT defaults — when non-zero, ``inflict_debuff`` seeds the holder's
    # ``<kind>_stack_cap`` / ``<kind>_per_stack_pct`` fields with these values
    # via max-merge on first apply. Designers tune from the EffectMeta entry
    # instead of editing Combatant field defaults; gear/build uplift on those
    # same Combatant fields still wins (max-merge keeps the larger value).
    # Only meaningful for effects with ``stack_kind`` set; ignored otherwise.
    # Leave at 0 to defer to whatever the Combatant carries (the legacy path
    # for burn/bleed/shock/poison whose defaults already flow through
    # balance.py constants + gear bonuses).
    stack_cap: int = 0
    per_stack_pct: float = 0.0
    # ── Declarative stacking (generic, data-driven) ─────────────────────────
    # ``stackable`` makes ``apply_effect`` ADD a stack on every (re)application
    # — capped at ``max_stack`` (folding in overrides / ``stack_cap_bonuses`` via
    # ``effective_stack_cap``) and stored in ``Combatant.effect_stacks`` keyed by
    # this effect — so a new stacking status needs NO bespoke ``<kind>_stacks``
    # field, hand-written increment, or expiry-reset line. Non-stackable effects
    # (the default) just refresh duration exactly as before. ``expire`` controls
    # the stacks when the duration ends: ``"drop"`` clears them all (today's
    # universal behavior); ``"decay"`` peels one stack per tick (reserved).
    # ``max_stack`` is the canonical cap for stackable effects; the older
    # ``stack_cap`` still drives the DoT (``stack_kind``) path.
    stackable: bool = False
    max_stack: int = 1
    expire: str = "drop"
    # Default chance to apply this effect when listed in a skill's
    # ``effects`` array. ``apply_skill_effects`` and ``apply_support_skill``
    # use it as the fallback when the skill JSON omits an explicit
    # ``effect_chances[<key>]``. Most effects stay at 1.0 (apply on hit);
    # set < 1.0 here for inherently probabilistic effects (e.g. a CC that
    # is supposed to land 35 % of the time by design rather than per-skill).
    apply_chance: float = 1.0
    # Whether Quang Thanh Tẩy (and any future cleanse source) can remove
    # this effect. Replaces the old string-substring filter
    # (``"Debuff" in key or "CC" in key``) with an explicit, data-driven
    # flag — ``EffectNgungDong`` and any other oddly-named debuff now flow
    # through correctly.
    #
    # Tri-state default: ``None`` defers to the kind-based default
    # (DEBUFF / CC → True, BUFF → False). Pass ``True`` to make a buff
    # cleansable, or ``False`` to make a debuff *uncleansable* (e.g.
    # DebuffTanDiet — Tận Diệt is meant to be irreversible). Explicit
    # values are preserved verbatim.
    cleansable: bool | None = None
    # Whether ApplyBuffSteal can rip this buff off the holder and stamp it
    # on the attacker. Tri-state: ``None`` defers to the kind-based default
    # — buffs default to ``True`` (every buff is fair game by default),
    # debuffs / CC default to ``False`` (steal semantics for negatives don't
    # apply — use ``cleansable`` for those instead). Pass an explicit value
    # to override (e.g. ``False`` on a unique mode buff that shouldn't leave
    # its owner — Lục Dục Cộng Minh is locked to its caster, etc.).
    stealable: bool | None = None
    # Whether this debuff/CC bounces back to the original attacker when the
    # defender carries ``reflect_pct`` (Kim Chung Tráo etc.). Default False
    # so existing debuffs stay opt-in. The bounce check fires in
    # ``inflict_debuff`` after the stamp succeeds: ``reflect_pct`` is treated
    # as the chance to mirror (not a damage multiplier). Recursion-guarded so
    # a reflected debuff can't re-bounce off the original attacker's reflect.
    # Stack-based DoTs (burn/bleed/shock/poison/chan_hoa…) should generally
    # stay False — their per-stack damage scales on caster stats, and
    # reflecting confuses the source attribution.
    reflectable: bool = False
    # When an effect expires naturally (duration tick → 0), automatically
    # apply this follow-up effect to the same holder. Tuple is
    # ``(effect_key, override_dict_or_None)``. Used for self-cycling passives
    # like Thiên Ma Giải Thể, where a Buff phase expires into a Vulnerable
    # phase, which expires back into the Buff — both metas point at each
    # other to form an infinite loop. Cleansed effects skip this chain
    # (handled in tick_effects, not in the cleanse path).
    on_expire_apply: tuple[str, dict | None] | None = None
    # One-shot damage dealt to the holder when this effect expires naturally
    # (NOT via cleanse — same gating as ``on_expire_apply``). Computed as
    # ``floor(holder.hp_max × expire_dmg_pct_hp_max)`` and applied directly to
    # HP (bypasses evasion / crit / DR / elemental res). ``expire_dmg_element``
    # is metadata for the log line and future proc hooks; falls back to
    # ``"physical"`` when unset. Used by ``DebuffCuonBay``'s Ngã Xuống tail —
    # the airborne window deals fall damage when the lift dissipates.
    expire_dmg_pct_hp_max: float = 0.0
    expire_dmg_element: str | None = None
    # Generic scaling rules — replace bespoke "placeholder key + manual
    # expansion in get_combat_modifiers" patterns with data-driven specs.
    # Each rule reads a value off the holder, optionally buckets/gates it,
    # and adds ``source × per_unit`` to an output stat. The per-unit
    # magnitude lives in ``stat_bonus`` under ``key``, so per-skill
    # ``effect_overrides[<effect>].stat_bonus[<key>]`` still wins via the
    # standard override flow.
    #
    # Rule shape (dict):
    #   key:         str   — either a stat_bonus placeholder name (popped from
    #                        ``result`` so per-skill overrides flow through),
    #                        or ``"field:<attr>"`` to read the per-unit magnitude
    #                        directly off the combatant (e.g. snapshotted
    #                        applier values like ``cuu_khuc_atk_reduce_active``).
    #   source:      str   — what to read from the holder (see _resolve_scaling_source)
    #   output:      str   — real stat key to write into (e.g. "hp_regen_pct")
    #   bucket:      float — optional, default 0. When > 0, floor(source/bucket) units.
    #   gate_source: str   — optional. When set, min/max check this value instead
    #                        of ``source`` — lets a flat ``source: "constant"``
    #                        rule gate on HP/MP without scaling by it.
    #   min:         float — optional gate, rule skipped when gate_value < min
    #   max:         float — optional gate, rule skipped when gate_value > max
    #   multiplier:  float — optional, default 1.0. Final output is
    #                        ``per_unit × units × multiplier``. Use -1.0 to
    #                        subtract from the output stat (debuff-style)
    #                        when the source magnitude is naturally positive.
    #
    # Sources:
    #   "constant"        — always 1.0 (flat-on-threshold pattern)
    #   "hp_pct"          — combatant.hp / hp_max (0.0 - 1.0)
    #   "hp_missing_pct"  — 1 - hp/hp_max
    #   "mp_pct"          — combatant.mp / mp_max
    #   "mp_missing_pct"  — 1 - mp/mp_max
    #   "stack:<name>"    — getattr(combatant, "<name>_stacks", 0) as float
    #
    # Convention: each rule's ``key`` must be unique across the whole
    # registry so the placeholder doesn't collide with another effect's
    # rule (the placeholder is popped from the aggregated stat dict after
    # the rule scales it, so a shared key would only fire once).
    scaling_rules: tuple[dict, ...] = ()
    # When set, any buff carrying this field auto-casts the named skill
    # back at the attacker every time the HOLDER (defender) successfully
    # evades. Skipped if the holder doesn't have ``proc_on_holder_evade_cast``
    # in their ``skill_keys`` (equipping gate). MP + cooldown of the named
    # skill still apply. Engine hook lives in
    # ``_fire_self_evade_procs`` — generalizes the old hardcoded Lưu Quang
    # Huyễn Ảnh → Cực Quang Trảm reactive into a reusable mechanism that
    # any future buff can opt into via this single field.
    proc_on_holder_evade_cast: str | None = None
    # Data-driven on-evade reactive block. When set, the holder's evade
    # event runs through the capability handlers in
    # ``src.game.systems.combat.evade_reactive`` (counter, inflict,
    # extend_self, proc_cast). Per-instance overrides via
    # ``effect_overrides[<buff>]._evade_react`` win over this default so
    # granting skills can customize tunables. Replaces the per-buff inline
    # branches in ``cast_skill`` (Lôi Quang Điện Ảnh, Ma Long Xuất Uyên).
    # ``proc_on_holder_evade_cast`` above stays as a legacy fallback for
    # buffs not yet migrated.
    evade_react: dict | None = None
    # Display emoji
    emoji: str = "✨"

    def __post_init__(self) -> None:
        # Frozen dataclass — bypass the freeze for the kind-based default.
        # Only resolves the ``None`` sentinel; explicit True / False set
        # by the caller passes through untouched.
        if self.cleansable is None:
            object.__setattr__(self, "cleansable", self.kind != EffectKind.BUFF)
        # Inverse default for stealable: BUFF → True (buffs are stealable by
        # default), DEBUFF / CC → False (don't apply to non-buffs). Explicit
        # values pass through untouched, same pattern as cleansable.
        if self.stealable is None:
            object.__setattr__(self, "stealable", self.kind == EffectKind.BUFF)


# ── Data-driven registry ──────────────────────────────────────────────────────
# Effect definitions live in src/data/effects/*.json. Mirrors the skill data
# pattern: drop a new entry in the JSON, no engine change needed. Loaded once
# at import; EffectMeta is rebuilt verbatim so all downstream behaviour is
# identical to the old in-Python literals.

_EFFECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "effects"

# JSON stores these as arrays; the dataclass wants tuples.
_TUPLE_FIELDS = ("aura_on_hit", "on_expire_apply", "scaling_rules")


def _meta_from_dict(d: dict) -> EffectMeta:
    """Rebuild an EffectMeta from one JSON entry.

    ``duration`` is folded into the JSON for designer ergonomics but is not
    an EffectMeta field — it is split back out into ``_DEFAULT_DURATIONS``.
    ``kind`` round-trips via the StrEnum; absent ``cleansable`` / ``stealable``
    stay unset so ``__post_init__`` re-derives the kind-based default.
    """
    kw = {k: v for k, v in d.items() if k != "duration"}
    kw["kind"] = EffectKind(kw["kind"])
    for tf in _TUPLE_FIELDS:
        v = kw.get(tf)
        if v is not None:
            kw[tf] = tuple(v)
    return EffectMeta(**kw)


def _load_effect_group(filename: str) -> tuple[list[EffectMeta], list[dict]]:
    raw = json.loads((_EFFECTS_DIR / filename).read_text(encoding="utf-8"))
    return [_meta_from_dict(d) for d in raw], raw


# One file per effect kind (designer clarity); util.json keeps the two
# instant-pulse regen effects separate. EFFECTS insertion order is the
# concatenation order below — nothing depends on it, but a stable order
# keeps diffs reviewable.
_BUFFS, _raw_buffs = _load_effect_group("buffs.json")
_DEBUFFS, _raw_debuffs = _load_effect_group("debuffs.json")
_CC, _raw_cc = _load_effect_group("cc.json")
_UTIL, _raw_util = _load_effect_group("util.json")

EFFECTS: dict[str, EffectMeta] = {
    m.key: m for m in _BUFFS + _DEBUFFS + _CC + _UTIL
}

# Split the folded-in durations back out into the legacy lookup map. Only
# entries that carried an explicit ``duration`` populate it — keys without
# one keep falling through to ``default_duration``'s ``3`` default exactly
# as before.
_DEFAULT_DURATIONS: dict[str, int] = {
    d["key"]: d["duration"]
    for d in (*_raw_buffs, *_raw_debuffs, *_raw_cc, *_raw_util)
    if "duration" in d
}


def default_duration(effect_key: str) -> int:
    """Return the default turn duration for an effect."""
    return _DEFAULT_DURATIONS.get(effect_key, 3)


# ── Stat computation helpers ──────────────────────────────────────────────────

def effective_stack_cap(combatant: "Combatant", effect_key: str) -> int:
    """Return the live stack cap for an effect on this combatant.

    Three sources fold in. The first two max-merge to form a "base cap";
    the third is additive on top so flat bonuses from gear/constitutions/
    Linh Căn/buffs stack predictably.

      1. ``EffectMeta.stack_cap`` — designer default in effects.py.
      2. Per-instance override on the holder:
         ``effect_overrides[<effect>].stack_cap`` — set by the skill JSON
         that applied the effect. Max-merged with (1) so a skill can only
         raise the floor, not shrink it.
      3. **Additive bonuses** from two sources:
         a) ``combatant.stack_cap_bonuses[<effect>]`` — flat integer added
            by gear / constitutions / Linh Căn at character-build time.
            Designed to be summed in ``character_stats.py`` from equip
            stats like ``cuu_khuc_stack_cap_bonus`` before combat starts.
         b) Active effect ``stat_bonus`` entries keyed
            ``"stack_cap_bonus:<effect>"`` — lets a buff grant a cap
            bonus while held. Per-skill ``stat_bonus`` overrides on the
            granting buff still win via the standard override flow.

    Burn/bleed/shock/poison are NOT routed through this helper for their
    base+gear cap math — they keep their dedicated ``<kind>_stack_cap``
    Combatant fields because the gear path was already wired before this
    helper existed. Calling ``effective_stack_cap`` on those keys still
    returns a useful value (meta + override + bonuses) but the gear path
    will not feed into it; new gear targeting them should keep writing
    to the existing ``<kind>_stack_cap`` field for now.
    """
    meta = EFFECTS.get(effect_key)
    # Stackable effects declare their cap via ``max_stack``; the DoT
    # (``stack_kind``) path keeps reading ``stack_cap``.
    if meta is None:
        base = 0
    elif getattr(meta, "stackable", False):
        base = int(meta.max_stack)
    else:
        base = int(meta.stack_cap)

    # Per-instance override on this effect's own entry (max-merge).
    override = combatant.effect_overrides.get(effect_key) or {}
    over_cap = int(override.get("stack_cap", 0) or 0)
    cap = max(base, over_cap)

    # Gear / constitution / Linh Căn flat bonus.
    bonus_dict = getattr(combatant, "stack_cap_bonuses", None)
    if bonus_dict:
        cap += int(bonus_dict.get(effect_key, 0))

    # Active-buff cap bonus — any active effect can grant ``+N stack cap``
    # by carrying ``stat_bonus["stack_cap_bonus:<effect_key>"] = N``. Per-
    # instance overrides on the granting effect win per-stat, mirroring
    # the standard ``get_combat_modifiers`` aggregation.
    bonus_key = f"stack_cap_bonus:{effect_key}"
    for active_key in combatant.effects:
        active_meta = EFFECTS.get(active_key)
        if active_meta is None:
            continue
        active_override = combatant.effect_overrides.get(active_key) or {}
        active_override_stats = active_override.get("stat_bonus") or {}
        if bonus_key in active_override_stats:
            cap += int(active_override_stats[bonus_key])
        elif bonus_key in active_meta.stat_bonus:
            cap += int(active_meta.stat_bonus[bonus_key])

    return cap


def effective_res_cap(combatant: "Combatant", element: str) -> float:
    """Return the live max-resistance cap for ``element`` on this combatant.

    Resolution:
      * Enemies (``combatant.key != "player"``) ALWAYS get
        ``MAX_ELEMENTAL_RES`` so JSON-tuned high res profiles
        (linh-căn apex 0.85 res, etc.) aren't silently nerfed.
      * Player: starts at ``RES_SOFT_CAP`` (0.75) and lifts one-for-one
        for every point of ``<element>_max_resist_bonus`` aggregated
        across the holder's gear/constitution/Linh Căn dict +
        active-buff ``stat_bonus`` contributions. Capped at
        ``MAX_ELEMENTAL_RES`` (0.90).

    Lets stats like ``hoa_max_resist_bonus: 0.15`` declare "+15% to the
    player's hoa cap" without bloating Combatant with one field per element.
    """
    from src.game.constants.balance import MAX_ELEMENTAL_RES, RES_SOFT_CAP
    if getattr(combatant, "key", None) != "player":
        return MAX_ELEMENTAL_RES
    bonus = float(combatant.element_max_resist_bonus.get(element, 0.0))
    bonus += float(
        get_combat_modifiers(combatant).get(f"{element}_max_resist_bonus", 0.0)
    )
    return min(MAX_ELEMENTAL_RES, RES_SOFT_CAP + bonus)


def count_elemental_dots(combatant: "Combatant", element: str) -> int:
    """Return the number of distinct active DoT effects on ``combatant``
    whose ``dot_element`` matches ``element``.

    Same gate as ``get_periodic_damage`` — counts only effects with an
    actual DoT damage path (``dot_pct > 0``, ``stack_kind`` set, or
    caster-stat-driven DoT). Non-tick effects with ``dot_element`` set
    (e.g. ``DebuffHoaXuyenThau``-style res-shred markers without an
    element tag) don't count.

    Used by per-cast damage amps that scale with fire-DoT count and by
    cleanse / proc hooks that fire once per distinct DoT kind.
    """
    return sum(
        1 for k in combatant.effects
        if (m := EFFECTS.get(k)) is not None
        and m.dot_element == element
        and (
            m.dot_pct > 0
            or m.stack_kind
            or m.dot_caster_hp_pct > 0
            or m.dot_caster_matk_scale > 0
        )
    )


def _resolve_scaling_source(combatant: "Combatant", source: str) -> float:
    """Read a scaling-rule source value off ``combatant``.

    Unknown sources return 0.0 so a typo in the data quietly disables the
    rule rather than crashing combat. Stack sources use the ``<name>_stacks``
    convention (e.g. ``"stack:phuong_hoa"`` → ``combatant.phuong_hoa_stacks``).

    The ``"constant"`` source always returns 1.0 — useful for threshold-
    gated flat bonuses where the magnitude is fixed and a separate
    ``gate_source`` carries the trigger condition.
    """
    if source == "constant":
        return 1.0
    if source == "hp_pct":
        hp_max = max(1, getattr(combatant, "hp_max", 1))
        return float(getattr(combatant, "hp", 0)) / hp_max
    if source == "hp_missing_pct":
        hp_max = max(1, getattr(combatant, "hp_max", 1))
        return max(0.0, 1.0 - float(getattr(combatant, "hp", 0)) / hp_max)
    if source == "mp_pct":
        mp_max = max(1, getattr(combatant, "mp_max", 1))
        return float(getattr(combatant, "mp", 0)) / mp_max
    if source == "mp_missing_pct":
        mp_max = max(1, getattr(combatant, "mp_max", 1))
        return max(0.0, 1.0 - float(getattr(combatant, "mp", 0)) / mp_max)
    if source.startswith("stack:"):
        name = source[6:]
        # New declarative stacks live in ``effect_stacks`` keyed by effect key;
        # legacy stacks keep their ``<name>_stacks`` Combatant attribute.
        es = getattr(combatant, "effect_stacks", None)
        if es and name in es:
            return float(es[name])
        return float(getattr(combatant, name + "_stacks", 0))
    if source.startswith("stat:"):
        # Direct read of a Combatant attribute. Lets a rule scale off raw
        # stats (evasion_rating, atk, matk, etc.) instead of resource %s
        # or stack counters. Unknown attrs return 0.0 so a typo disables
        # the rule cleanly. Used by BuffCuongPhongHoThe — evasion_rating
        # converts to final_dmg_reduce.
        attr = source[5:]
        return float(getattr(combatant, attr, 0.0))
    return 0.0


def _apply_scaling_rules(combatant: "Combatant", result: dict) -> None:
    """Expand every active effect's ``scaling_rules`` into ``result``.

    For each rule on each active effect:
      1. Resolve the per-unit magnitude. When ``rule['key']`` starts with
         ``"field:"`` the magnitude is read directly off the combatant
         (e.g. snapshotted applier values like
         ``cuu_khuc_atk_reduce_active``); otherwise it's popped from
         ``result`` (a stat_bonus placeholder, with per-skill overrides
         already folded in by the main aggregation loop).
      2. Resolve the source value off the holder (for scaling).
      3. Resolve the gate value — defaults to the source, or reads
         ``gate_source`` when set (lets a flat-on-threshold rule scale
         by ``"constant"`` while gating on, say, ``"hp_pct"``).
      4. Apply min/max gates — rule skipped if gate value out of range.
      5. Apply bucketing — when ``bucket > 0``, ``units = floor(source / bucket)``;
         otherwise ``units = source`` (good for integer stack sources).
      6. Add ``per_unit × units × multiplier`` to ``result[output]``.
         ``multiplier`` (default 1.0) lets a rule flip sign without
         requiring the source magnitude to be negative — useful when
         the magnitude comes from a naturally-positive field but the
         output is a reduction (e.g. ``atk_pct -= cuu_khuc_atk_reduce``).
    """
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta or not meta.scaling_rules:
            continue
        for rule in meta.scaling_rules:
            key = rule["key"]
            if key.startswith("field:"):
                per_unit = float(getattr(combatant, key[6:], 0.0))
            else:
                per_unit = float(result.pop(key, 0.0))
            if per_unit == 0:
                continue
            source_value = _resolve_scaling_source(combatant, rule["source"])
            gate_value = (
                _resolve_scaling_source(combatant, rule["gate_source"])
                if rule.get("gate_source") else source_value
            )
            lo, hi = rule.get("min"), rule.get("max")
            if lo is not None and gate_value < lo:
                continue
            if hi is not None and gate_value > hi:
                continue
            bucket = float(rule.get("bucket", 0.0))
            units = int(source_value / bucket) if bucket > 0 else source_value
            if units <= 0:
                continue
            multiplier = float(rule.get("multiplier", 1.0))
            contribution = per_unit * units * multiplier
            # Optional ``max_output`` clamps the per-rule contribution by
            # absolute value. Useful when source scales unboundedly (e.g.
            # ``stat:evasion_rating``) but the output stat needs a ceiling
            # (e.g. final_dmg_reduce capped at 0.30). Sign-preserving so
            # a negative ``multiplier`` rule still respects the bound.
            max_output = rule.get("max_output")
            if max_output is not None:
                mo = abs(float(max_output))
                if contribution > mo:
                    contribution = mo
                elif contribution < -mo:
                    contribution = -mo
            out_key = rule["output"]
            result[out_key] = result.get(out_key, 0.0) + contribution


# ── Skill Mastery: magnitude stat_bonus allowlist ───────────────────────────
# Only these stat_bonus keys are scaled by a buff/debuff's stamped
# ``_mastery_mult`` in get_combat_modifiers. Fixed names plus the element-
# suffixed families (res_<elem>, <elem>_max_resist_bonus, <elem>_dmg_taken).
# Everything else (config-only keys, scaling_rule placeholders, _mastery_mult
# itself) is left untouched. ``_mastery_scalable`` early-outs in the read path
# only run when a non-1.0 mult is stamped, so this list never executes for the
# flag-off / enemy / non-player case.
_MASTERY_SCALABLE_STATS: frozenset[str] = frozenset({
    "final_dmg_bonus",
    "final_dmg_reduce",
    "crit_rating",
    "crit_dmg_rating",
    "evasion_rating",
    "crit_res_rating",
    "hp_regen_pct",
    "shield_regen_pct",
    "mp_regen_pct",
    "spd_pct",
    "res_all",
    "dot_taken_bonus",
})
_MASTERY_SCALABLE_SUFFIXES: tuple[str, ...] = (
    "_max_resist_bonus",  # <elem>_max_resist_bonus
    "_dmg_taken",         # <elem>_dmg_taken
)


def _mastery_scalable(stat: str) -> bool:
    """True if ``stat`` is a magnitude stat_bonus key the mult should scale."""
    if stat in _MASTERY_SCALABLE_STATS:
        return True
    if stat.startswith("res_"):  # res_all already covered; res_<elem> here
        return True
    return stat.endswith(_MASTERY_SCALABLE_SUFFIXES)


# ── Config-only stat_bonus keys ─────────────────────────────────────────────
# Stamped inside ``stat_bonus`` so designers can tune them in JSON, but consumed
# by hooks elsewhere (casting / on-evade / inflict_debuff / per-turn aura
# refreshers) rather than applied as real stats. ``get_combat_modifiers`` pops
# them so they never masquerade as a stat, and ``displayable_stat_bonus`` hides
# them from the skill browser so it never renders "+0 <raw_key>" noise.
# Scaling-rule placeholders are handled separately (popped by
# ``_apply_scaling_rules``; filtered for display via each rule's ``key``).
_CONFIG_ONLY_STAT_KEYS: frozenset[str] = frozenset({
    # Quỷ Ảnh Mê Tung — stack cap (read by the on-evade hook).
    "quy_anh_max_stacks",
    # Nhược Thủy Ấn — detonation config (read by casting.py).
    "thuy_mark_detonate_lost_hp_pct",
    # Tuyệt Diệu Vô Ảnh — slow-immune gate flag (read by inflict_debuff).
    "slow_immune",
    # Ma Long Xuất Uyên — counter-strike config (read by cast_skill).
    "ma_long_counter_base_dmg",
    "ma_long_counter_matk_pct",
    "ma_long_counter_debuff_chance",
    # Thủy Vi — shield-bypass charge counters (read by cast_skill).
    "thuy_vi_charges",
    "thuy_vi_bypass_pct",
    # Lưu Ly Tịnh Hỏa — per-roll cleanse chance (periodic hook).
    "luu_ly_cleanse_chance",
    # Lưu Tinh Cản Nguyệt — refresh-hook config.
    "_lt_base_spd_pct",
    "_lt_per_dot_evasion",
    "_lt_per_dot_spd_pct",
    # Liễu Nhứ Tùy Phong — drift base magnitudes.
    "_ln_base_spd_pct",
    "_ln_base_evasion",
    # Bộ Bộ Sinh Liên — heal-taken→spd/eva conversion + caps.
    "_bb_spd_per_htb",
    "_bb_eva_per_htb",
    "_bb_spd_cap",
    "_bb_eva_cap",
    # Xuân Thu Nhất Bút — season-rotation per-season magnitudes.
    "_xt_spring_hp_regen_pct",
    "_xt_spring_final_dmg_reduce",
    "_xt_spring_shield_regen_pct",
    "_xt_autumn_final_dmg_bonus",
    "_xt_autumn_crit_dmg_rating",
    "_xt_autumn_crit_rating",
    # Mộc Linh Cộng Sinh — banked overheal reservoir (read by the heal-capture
    # hook + the priority-48 periodic release). Lives at the override TOP level
    # (not in stat_bonus), but listed here defensively so it never leaks as a
    # stat even if a future code path copies it into stat_bonus.
    "_sap",
    # Thiên Cương Phá Sát Thể — Kim killing-body config (read by run_on_hit_procs):
    # L3 Phá Giáp on-hit chances + L9 Kiếm Lãng splash gate/coefficients. Stamped
    # in the constitution's milestone stat_bonuses so designers tune them in JSON,
    # but consumed by the proc sweep rather than applied as real stats.
    "kim_pha_giap_on_hit_chance",
    "kim_pha_giap_high_sat_khi_chance",
    "kim_sword_splash_at_max_sat_khi",
    "kim_sword_splash_base_chance",
    "kim_sword_splash_crit_coeff",
    "kim_sword_splash_chance_cap",
    # Thái Bạch Canh Kim Thể — Kim crit-bleeder config: L6 equipment-stat
    # amplifier (consumed by the build-time pre-pass in character_stats), L3
    # one-shot hit-count arm (seeded at build, consumed in cast_skill), and L9
    # anti-bleed crit amps + periodic-crit cadence (read onto Combatant fields,
    # threaded into the crit step / PRE_TURN hook). None ever render as a stat.
    "equip_stat_amp_pct",
    "next_skill_hit_count",
    "kim_bleed_hunter_crit_chance_bonus",
    "kim_bleed_hunter_crit_dmg_bonus",
    "kim_periodic_crit_interval",
    # Huyền Âm Thiên Ma Thể — Ám shadow-mage config: L1 shadow-stack gate, L6
    # Nhập-Ma damage/stat-steal magnitude (read onto Combatant, consumed by the
    # POST_HIT sweep + combat_hit), L9 auto-Nhập-Ma cadence (PRE_TURN hook).
    # None ever render as a stat.
    "shadow_stack_on_hit",
    "nhap_ma_dmg_bonus",
    "am_auto_nhap_ma_interval",
    "am_nhap_ma_duration",
    # Chân Dương Bất Diệt Thể — Hỏa phoenix config: L6 chance-gated burning amp
    # (rolled at cast assembly) + L9 3-charge upgraded-revive config (read by
    # the priority-5 ON_REVIVE hook). None ever render as a stat.
    "hoa_burning_amp_chance",
    "hoa_burning_amp_pct",
    "hoa_revive_upgraded",
    "hoa_revive_charges",
    "hoa_revive_hp_pct_l9",
    # Huyền Thủy Trường Sinh Thể — Thủy tidal counter-puncher config: L1 intake
    # fraction + reservoir cap scale, L3 retaliate-freeze chance, L6 shatter
    # fraction, L9 tidal-flood cadence/scaling. Read onto Combatant fields,
    # consumed by apply_reactive_damage / run_on_hit_procs / the PERIODIC hook.
    # None ever render as a stat.
    "thuy_tide_intake_pct",
    "thuy_reservoir_cap_matk_scale",
    "thuy_retaliate_freeze_chance",
    "thuy_shatter_tide_pct",
    "thuy_tidal_flood_enabled",
    "thuy_tidal_flood_interval",
    "thuy_tidal_release_pct",
    "thuy_tidal_depth_per_turn",
    "thuy_tidal_depth_mult_cap",
    "thuy_tidal_refill_pct",
    # Trường Xuân Linh Mộc Thể — Mộc poison/eternal-spring config: L3 vs-slowed
    # damage bonus (read in combat_hit), L6 debuff-count regen (PERIODIC hook),
    # L9 guaranteed-poison + Undying Spring cheat-death (run_on_hit_procs /
    # ON_REVIVE hook). Read onto Combatant fields; none ever render as a stat.
    # ``poison_on_hit_pct`` is NOT here — it's a real on-hit chance attr like
    # ``burn_on_hit_pct``.
    "moc_vs_slowed_dmg_bonus",
    "moc_regen_per_enemy_debuff",
    "moc_regen_debuff_cap",
    "moc_guaranteed_poison_on_attack",
    "moc_guaranteed_poison_stacks",
    "moc_undying_spring_enabled",
    "moc_undying_cooldown_turns",
    "moc_undying_min_hp",
    "moc_undying_heal_reduce_gate",
    # Hoàng Cổ Thánh Thể (Universal Saint Body) — config keys consumed by
    # the PRE_TURN aura hook (hoang_co.py) and on-hit procs. The three
    # runtime counters (saint_crit_turn_counter / saint_realm_turn_counter /
    # saint_crit_armed) are Combatant-only and never appear in stat_bonus.
    "saint_qilin_cleanse_chance",
    "saint_periodic_crit_interval",
    "saint_mp_on_hit_pct",
    "saint_realm_enabled",
    "saint_realm_interval",
    "saint_realm_duration",
    # Phi Thiên Lăng Vân Thể (Phong dodge-counter bruiser) — config keys
    # consumed by on-evade / on-hit hooks, cast_skill unevadable gate, and
    # post-hit Cuốn Bay proc. Runtime counters (phong_van_stacks,
    # phong_crit_armed, phong_unevadable_armed, phong_skill_cast_counter)
    # are Combatant-only and never appear in stat_bonus.
    "phong_eva_phong_dmg_per_300",
    "phong_van_dodge_stack",
    "phong_dodge_arms_crit",
    "phong_dodge_crit_applies_an_phong",
    "phong_unevadable_interval",
    "phong_cuon_bay_on_crit_chance",
    # Thiên Lôi Cường Thể (Lôi shock/speed nuker) — config keys consumed by the
    # crit POST_HIT (te-liet + reflex), the DoT-tick loop (charge burst + true
    # rider), combat_hit (speed-advantage), and the L9 true-dmg rider. The
    # ``loi_charge`` counter is runtime-only (Combatant) and never in stat_bonus.
    "loi_te_liet_on_crit_chance",
    "loi_charge_enabled",
    "loi_spd_advantage_per_10",
    "loi_spd_advantage_cap",
    "loi_reflex_bonus_attack",
    "loi_bonus_true_dmg_pct",
    # Tịnh Quang Hộ Pháp Thể (Quang guardian) — config keys consumed by the blind
    # proc loop (Thánh Quang stack), the PRE_TURN self-cleanse aura, the build-time
    # guardian-summon spawn, and the L9 judgment POST_HIT. Runtime counters
    # (thanh_quang_stacks, quang_cleanse_turn_counter) are Combatant-only.
    "quang_blind_stack",
    "quang_self_cleanse_interval",
    "quang_self_cleanse_count",
    "quang_guardian_summon_matk_pct",
    "quang_judgment_strip_chance",
    "quang_judgment_applies_pha_giap",
    # Hỗn Nguyên Vô Cực Thể (Universal omni-element amplifier) — config keys read
    # off Combatant fields by combat_hit (the sum-all-elements branch) and by the
    # cast damage roll (the res-ignore proc).
    "omni_sum_element_dmg",
    "omni_res_ignore_chance",
    # Thiên Địa Nhân Hòa Thể (Universal Hòa Khí stack-scaler) — config keys read
    # by the PERIODIC nhan_hoa hook (stack increment, L6 backlash, L9 cleanse).
    # ``harmony_stacks`` is a runtime counter (Combatant-only) read by the L1/L3/L9
    # scaling_rules but never appears in stat_bonus.
    "harmony_stack_per_turn",
    "harmony_stack_cap",
    "harmony_backlash_pct_per_stack",
    "harmony_backlash_min_stacks",
    "harmony_l9_cleanse",
    # Bắc Minh Băng Phách Thể (Thủy disruptor) — config keys read off Combatant
    # fields by run_on_hit_procs (bidirectional procs + Hàn Khí burst).
    "bm_cold_aura_enabled",
    "bm_freeze_on_attack_chance",
    "bm_mp_drain_pct",
    "bm_mp_drain_heal_pct",
    "bm_han_khi_cap",
    "bm_heal_reduce_chance",
    "bm_heal_reduce_vs_frozen_chance",
    "bm_burst_freeze_turns",
    "bm_burst_drain_pct",
    # DebuffCucHan carries this multiplicative regen cut (read by the regen hook
    # via ``regen_reduce_pct`` below); kept out of get_combat_modifiers so it
    # never renders as a stat or feeds an additive aggregate.
    "regen_reduce_pct",
    # Huyền Minh Nhược Thể (Thủy attrition disruptor) — config keys read off
    # Combatant fields by casting.py (L1 physical-only reduce + per-cast L9
    # corrosion + on-evade Uyên) and run_huyen_minh_procs (L3 drain + L9 drown
    # burst). ``phys_dmg_reduce_pct`` is read direct off the Combatant field, so
    # it's popped here too (never an aggregated stat). Runtime counters
    # (hm_uyen_stacks, hm_mp_drained_total) are Combatant-only and never in
    # stat_bonus; ``hm_uyen_per_stack_evasion`` is a scaling-rule placeholder on
    # BuffHuyenMinhHuTinh (popped by _apply_scaling_rules, not here).
    "phys_dmg_reduce_pct",
    "hm_mp_drain_pct",
    "hm_mp_drain_heal_pct",
    "hm_hp_siphon_pct",
    "hm_uyen_cap",
    "hm_corrode_poison_stacks",
    "hm_corrode_bleed_stacks",
    "hm_drown_burst_drain_pct",
    # Thiên Thủy Thánh Thể (Thủy holy-spring sustain tank) — config keys read off
    # Combatant fields by apply_reactive_damage (L3 convert/reflect), _apply_heal
    # (L6 cleanse + Tịnh Hóa), and the casting defender block (L9 abyss swallow).
    # ``res_thuy``/``final_dmg_reduce`` are NOT here — they're real aggregated
    # stats. ``tt_tinh_hoa_stacks`` is a runtime counter, never in stat_bonus.
    "tt_dmg_convert_heal_pct",
    "tt_reflect_remainder_pct",
    "tt_heal_cleanse_chance",
    "tt_tinh_hoa_cap",
    "tt_tinh_hoa_per_stack_cleanse",
    "tt_tinh_hoa_mp_on_heal_pct",
    "tt_abyss_threshold",
    "tt_abyss_reduce_pct",
    "tt_abyss_reflect_pct",
    # Lưu Ly Thuẫn Thân Thể (universal shield-only aegis body) — config keys
    # consumed at build (compute_combat_stats HP→shield conversion) and in
    # take_damage (force-shield + L9 reform) / _apply_heal (L3 heal→shield).
    # ``damage_bonus_from_shield_pct`` is NOT here — it's a real stat (L6 reuses
    # it). ``aegis_reform_just_triggered`` is a runtime flag, never in stat_bonus.
    "shield_only_body",
    "shield_from_hp_max_pct",
    "heal_to_shield_pct",
    "aegis_reform_charges",
    "aegis_reform_shield_pct",
    # Liệt Diễm Phần Thiên Thể (Hỏa fire nuker) — config keys read off Combatant
    # fields by periodic/lietdiem.py (ramp + avatar cadence) and apply_reactive_damage
    # (L6 fire-absorb). The ramp/absorb stat OUTPUTS (matk_pct/crit_rating/dmg_bonus_hoa)
    # are real stats via scaling_rules; ``dot_can_crit`` (L9) is a real stat too.
    # Runtime counters (lietdiem_*_stacks / _counter) are Combatant-only.
    "lietdiem_burn_per_turn",
    "lietdiem_burn_cap",
    "lietdiem_van_hoa_absorb",
    "lietdiem_van_hoa_cap",
    "lietdiem_avatar_enabled",
    "lietdiem_avatar_interval",
    "lietdiem_avatar_duration",
    # Niết Bàn Bất Diệt Thể (Hỏa lifesteal-res nirvana berserker) — config keys.
    # ``hoa_overcap_to_dmg`` is read build-time (overcap → element_dmg_bonus.hoa);
    # the niet_ban_revive_* / post_revive / nb_nghiep_* keys by the ON_REVIVE +
    # PERIODIC hooks. The per-tier OUTPUTS (element_dmg_bonus.hoa / dot_dmg_bonus)
    # are real fields the hook mutates directly. Runtime counters (nb_nghiep_tier /
    # _progress / niet_ban_revive_used) are Combatant-only.
    "hoa_overcap_to_dmg",
    "nb_nghiep_accumulate",
    "nb_nghiep_revive_pct_per_tier",
    "niet_ban_revive_enabled",
    "niet_ban_revive_pct",
    "niet_ban_revive_clear_debuffs",
    "niet_ban_post_revive_boost",
    # Hậu Thổ Thần Thể (Thổ HP-vampire growth juggernaut) — config keys read by
    # run_hau_tho_procs (steal), casting.py (L3 true-dmg), revives.py (L9). The
    # L3 OUTPUT is direct true-damage, not a stat. Runtime counters
    # (hau_tho_stolen_total / _tier / _steal_progress / _rebirth_used /
    # _tier10_applied) are Combatant-only.
    "hau_tho_hp_steal_pct",
    "hau_tho_accumulate",
    "hau_tho_dmg_from_maxhp_pct",
    "hau_tho_dmg_from_shield_pct",
    "hau_tho_rebirth_enabled",
    # Thánh Sơn Bất Động Thể (Thổ immovable fortress) — config keys read by
    # run_thanh_son_procs (Kiên Cố stack + Bào Mòn/Stun riders), casting.py (L3
    # true-dmg), take_damage (L9 survive). The L1 per-stack res/DR OUTPUTS come
    # from BuffKienCo's scaling_rules (real stats). Runtime counters
    # (thanh_son_kien_co_stacks / thanh_son_immovable_just_triggered) are
    # Combatant-only.
    "thanh_son_kien_co_on_hit",
    "thanh_son_kien_co_cap",
    "thanh_son_dmg_from_shield_pct",
    "thanh_son_l3_full_bonus",
    "thanh_son_bao_mon_chance",
    "thanh_son_stun_chance",
    "thanh_son_stun_turns",
    "thanh_son_immovable_enabled",
    "thanh_son_survive_shield_pct",
    # Kim Cang Bất Hoại Thể (Thổ indestructible shield body) — config keys
    # consumed by the PERIODIC regen hook (dia_mach increment), the casting
    # defender-step (L3 physical negate), and the two PERIODIC aura hooks
    # (L6 auto-slow, L9 earth-aura). ``dia_mach_stacks`` is a runtime counter
    # (Combatant-only) and never appears in stat_bonus.
    "dia_mach_per_regen",
    "tho_phys_immune_chance",
    "tho_phys_immune_high_shield_chance",
    "tho_phys_immune_shield_gate",
    "tho_auto_slow_enabled",
    "tho_earth_aura_shield_pct",
    # Thiên Kiếp Vạn Lôi Thể (Lôi tribulation CC-lockdown executioner) — config
    # keys read off Combatant fields by casting.py (per-cast Vạn Lôi accrual +
    # sustained ``tk_extra_hits``), run_thien_kiep_procs (L3 CC riders, L9
    # execute, defender stack-on-struck) and build_defense_stats (attacker-side
    # ``tk_evasion_shred_pct``). The per-stack OUTPUTS (crit_dmg_rating /
    # spd_pct) come from BuffVanLoi's scaling_rules (real stats). Runtime
    # counter (tk_van_loi_stacks) is Combatant-only.
    "tk_stack_on_cast",
    "tk_stack_on_struck",
    "tk_van_loi_cap",
    "tk_soc_dien_chance",
    "tk_te_liet_chance",
    "tk_stun_chance",
    "tk_stun_stack_gate",
    "tk_stun_turns",
    "tk_evasion_shred_pct",
    "tk_extra_hits",
    "tk_execute_chance",
    "tk_execute_hp_pct",
    "tk_execute_stack_gate",
})


def displayable_stat_bonus(meta: "EffectMeta") -> dict[str, float]:
    """A buff/debuff's ``stat_bonus`` minus engine-internal keys.

    Drops config-only keys (``_CONFIG_ONLY_STAT_KEYS``), this effect's
    ``scaling_rules`` placeholders, and any ``_``-prefixed internal key — so
    player-facing surfaces render only real stat modifiers instead of raw
    placeholders like ``+0 _xt_autumn_crit_rating`` or
    ``-0 thuy_mark_per_stack_res``.
    """
    placeholders = {rule["key"] for rule in meta.scaling_rules}
    return {
        stat: val
        for stat, val in meta.stat_bonus.items()
        if not stat.startswith("_")
        and stat not in _CONFIG_ONLY_STAT_KEYS
        and stat not in placeholders
    }


def get_combat_modifiers(combatant: "Combatant") -> dict[str, float]:
    """Aggregate all active effect stat bonuses/penalties on a combatant.

    Per-instance overrides (``combatant.effect_overrides[key]['stat_bonus']``)
    win on a per-stat basis: if a heavy skill stamped DebuffXeRach with
    ``res_all: -0.20``, that supersedes the meta default ``-0.08``. Stats
    not present in the override fall back to the meta value, so a skill
    can override one stat without nuking the rest.

    Summon auras (see ``skill_extras.aura_stat_bonus``) are folded in here
    too: each active summon contributes its own copy of its ``aura_buff``,
    so multi-summon strategies stack additively even when every summon
    shares the same aura key.

    Returns a dict with signed float values per stat key:
      final_dmg_bonus, final_dmg_reduce, crit_rating, crit_dmg_rating,
      evasion_rating, crit_res_rating, spd_pct, hp_regen_pct, res_all
    """
    result: dict[str, float] = {}
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        override = combatant.effect_overrides.get(effect_key) or {}
        override_stats = override.get("stat_bonus") or {}
        # Skill Mastery: a mastered cast stamps ``_mastery_mult`` onto this
        # buff/debuff override. mm == 1.0 (absent, enemy, flag-off) → the
        # original byte-identical path; otherwise allowlisted magnitude
        # contributions are scaled. Downstream caps clamp as before.
        mm = override.get("_mastery_mult", 1.0)
        if mm == 1.0:
            for stat, val in meta.stat_bonus.items():
                effective = override_stats.get(stat, val)
                result[stat] = result.get(stat, 0.0) + effective
            # Stats present only in the override (not in the meta) still apply.
            for stat, val in override_stats.items():
                if stat not in meta.stat_bonus:
                    result[stat] = result.get(stat, 0.0) + val
        else:
            for stat, val in meta.stat_bonus.items():
                effective = override_stats.get(stat, val)
                if _mastery_scalable(stat):
                    effective *= mm
                result[stat] = result.get(stat, 0.0) + effective
            for stat, val in override_stats.items():
                if stat not in meta.stat_bonus:
                    if _mastery_scalable(stat):
                        val *= mm
                    result[stat] = result.get(stat, 0.0) + val

    # Lazy import to break the cycle: skill_extras → combatant → effects.
    if combatant.summons:
        from src.game.systems.combat.skill_extras import aura_stat_bonus
        for stat, val in aura_stat_bonus(combatant).items():
            result[stat] = result.get(stat, 0.0) + val
    # Generic ``scaling_rules`` expansion — runs after the main stat_bonus
    # aggregation so each rule can read its per-unit magnitude (the
    # ``key`` placeholder) out of ``result`` with all per-skill overrides
    # already folded in. The placeholder key is popped after scaling so it
    # doesn't leak as a "real" stat. New effects should prefer this path
    # over hand-coding a new ``result.pop(...)`` block below.
    _apply_scaling_rules(combatant, result)

    # Config-only keys — stamped in ``stat_bonus`` so designers can tune
    # without touching engine constants, but consumed by hooks elsewhere
    # (casting / on-evade / inflict_debuff). Drop them here so they never
    # masquerade as real stats. Scaling-rule placeholders are popped by
    # ``_apply_scaling_rules``; this list is only for non-scaling config.
    # Intersect from the (small) result side — this is THE combat hot path
    # (~30 calls/turn) and the config-key list grows with every body, so
    # iterating the full list per call costs millions of no-op pops.
    for cfg_key in _CONFIG_ONLY_STAT_KEYS & result.keys():
        del result[cfg_key]
    return result


def regen_reduce_pct(combatant: "Combatant") -> float:
    """Strongest multiplicative regen cut from active effects (Cực Hàn Phong Ấn).

    ``DebuffCucHan`` carries ``regen_reduce_pct: 0.90`` in its stat_bonus; the
    regen hook multiplies HP/MP/shield regen by ``1 - this``. Read directly off
    the effect metas (not via ``get_combat_modifiers``, which pops the key as
    config-only). Returns the MAX so stacking sources don't compound past 100%.
    0.0 for every combatant carrying no such debuff (the default everywhere).
    """
    reduce = 0.0
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if meta is not None:
            reduce = max(reduce, float(meta.stat_bonus.get("regen_reduce_pct", 0.0)))
    return min(1.0, reduce)


def effective_phys_dmg_reduce(combatant: "Combatant") -> float:
    """Physical-only damage reduction from the static field AND active effects.

    ``phys_dmg_reduce_pct`` is read DIRECTLY off the Combatant field in casting.py
    (it's config-only, so get_combat_modifiers pops it). Body #15 sets the field
    permanently from the constitution; body #17's Hỏa Thần avatar grants it via a
    temporary BUFF (BuffHoaThanHoaThan) whose stat_bonus carries the key — so the
    casting hook must also see active-effect contributions. Returns the MAX of the
    field and any active effect's ``phys_dmg_reduce_pct`` (the casting hook caps it
    at 0.75 / MAX_PHYS_REDUCTION). 0.0 for every combatant carrying neither.
    """
    reduce = float(getattr(combatant, "phys_dmg_reduce_pct", 0.0))
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if meta is not None:
            reduce = max(reduce, float(meta.stat_bonus.get("phys_dmg_reduce_pct", 0.0)))
    return reduce


def get_periodic_damage(
    combatant: "Combatant", rng: random.Random | None = None,
) -> list[tuple[str, int, bool]]:
    """Return list of (effect_key, damage, is_crit) for all active DoT effects.

    Policy: filters out non-DoT effects and poison on poison-immune holders,
    then delegates the per-tick math to
    ``src.game.engine.damage.dot.calculate_dot_damage``.

    Per-instance overrides (``effect_overrides[key]['dot_pct'/'dot_element']``)
    are folded into the meta passed downstream so a skill that stamps
    DebuffThieuDot with ``dot_pct=0.08`` ticks for 2× the meta default.
    """
    from src.game.engine.damage.dot import calculate_dot_damage

    rng = rng or random.Random()
    results: list[tuple[str, int, bool]] = []
    for effect_key in list(combatant.effects.keys()):
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        override = combatant.effect_overrides.get(effect_key) or {}
        effective_meta = _meta_with_override(meta, override)
        # Skill Mastery: scale the classic %HP ``dot_pct`` by the holder's
        # stamped ``_mastery_mult`` (early-out on 1.0). Stack-DoT per_stack_pct
        # is deferred (see _meta_with_override).
        # TODO(mastery 3b-followup): scale stack-DoT ``per_stack_pct`` (global
        # Combatant field — coupling hazard) and caster-stat DoT formulas.
        mm = override.get("_mastery_mult", 1.0)
        if mm != 1.0 and effective_meta.dot_pct > 0:
            from dataclasses import replace as _replace
            effective_meta = _replace(
                effective_meta, dot_pct=effective_meta.dot_pct * mm,
            )
        # Gate accepts classic %HP DoTs (``dot_pct > 0``), stack-based DoTs
        # (``stack_kind`` set, dot_pct stays 0 in the data because the tick
        # comes from per-stack counters), and caster-stat-driven DoTs
        # (``dot_caster_hp_pct`` / ``dot_caster_matk_scale`` set — Lục Hồn
        # Chú-class curses where the formula reads the applier's stats).
        if (
            effective_meta.dot_pct <= 0
            and not effective_meta.stack_kind
            and effective_meta.dot_caster_hp_pct <= 0
            and effective_meta.dot_caster_matk_scale <= 0
            and effective_meta.dot_target_hp_pct <= 0
        ):
            continue
        if effect_key == EffectKey.DEBUFF_DOC_TO and combatant.poison_immunity:
            continue
        dmg, is_crit = calculate_dot_damage(combatant, effect_key, effective_meta, rng)
        results.append((effect_key, dmg, is_crit))
    return results


def _meta_with_override(meta: EffectMeta, override: dict) -> EffectMeta:
    """Return a copy of ``meta`` with override values applied.

    Only ``dot_pct`` and ``dot_element`` are spliced here — stat_bonus
    overrides are handled in ``get_combat_modifiers`` so they don't have
    to allocate a new EffectMeta on every modifier query.
    """
    if not override or not any(k in override for k in ("dot_pct", "dot_element")):
        return meta
    from dataclasses import replace
    return replace(
        meta,
        dot_pct=float(override.get("dot_pct", meta.dot_pct)),
        dot_element=override.get("dot_element", meta.dot_element),
    )


def check_cc_skip_turn(
    combatant: "Combatant", rng: random.Random
) -> str | None:
    """Return the CC effect key that causes the combatant to skip their turn.

    Returns None if the combatant can act normally. Effects with
    ``skips_turn`` skip deterministically; effects with a non-zero
    ``skip_turn_chance`` (e.g. paralysis 0.50, fear 0.20) roll once per
    turn — both declared in the effect JSON data, no per-key branches.
    """
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        if meta.skips_turn:
            return effect_key
        if meta.skip_turn_chance > 0.0 and rng.random() < meta.skip_turn_chance:
            return effect_key
    return None


def check_prevents_skills(combatant: "Combatant") -> str | None:
    """Return the CC effect key that prevents skill use, or None."""
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if meta and meta.prevents_skills:
            return effect_key
    return None


def check_attack_miss(combatant: "Combatant", rng: random.Random) -> bool:
    """Return True if the attacker's strike whiffs due to a blind-style effect.

    Any active effect with a non-zero ``miss_chance`` (e.g. DebuffLoaMat)
    rolls once per swing — the attacker still pays MP/CD, the swing just
    fails to land. The chance is declared in the effect JSON data.
    """
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if meta is not None and meta.miss_chance > 0.0:
            return rng.random() < meta.miss_chance
    return False


def format_active_effects(combatant: "Combatant") -> str:
    """Format active effects for display in Discord embeds."""
    if not combatant.effects:
        return "—"
    parts: list[str] = []
    for key, turns in combatant.effects.items():
        meta = EFFECTS.get(key)
        name = meta.vi if meta else key
        emoji = meta.emoji if meta else "❓"
        parts.append(f"{emoji}{name}({turns}t)")
    return " ".join(parts)


# Linh Căn combat procs have moved to src.game.engine.linh_can_effects
# (per-element modules + package-level orchestrators). Combat imports them
# directly from there — nothing in this file references them anymore.
