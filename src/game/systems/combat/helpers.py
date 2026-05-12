"""Module-level helpers used across the combat package.

Contains only stateless utilities: no session reference, no Discord IO.
Keeping them here breaks the import cycle between session/casting/procs.
"""
from __future__ import annotations

from src.game.constants.balance import SPD_EXTRA_TURN_MAX_PCT, SPD_EXTRA_TURN_SCALE
from src.game.constants.effects import EffectKey
from src.game.engine.effects import get_combat_modifiers
from src.game.systems.combatant import Combatant


def effective_mp_cost(actor: Combatant, skill_data: dict) -> int:
    """Compute the actual MP cost a skill charges this actor.

    Reads the skill's element and adds ``element_mp_cost_mult`` (extra
    fraction on top of base 1.0×) for that element to amplify the cost.
    Used by Thiên Nhất Sinh Thủy Trận and any future "this formation
    amplifies element X's MP outlay" mechanic. The damage formula
    ``DMG = base + mp_cost`` reads the same value, so the extra MP also
    boosts damage proportionally — a deliberate tradeoff.

    Returns base ``mp_cost`` unchanged when the skill has no element or
    no multiplier is set, so vanilla casts pay zero overhead.
    """
    base = int(skill_data.get("mp_cost", 0))
    if base <= 0:
        return base
    elem = skill_data.get("element")
    if not elem:
        return base
    extra = float(actor.element_mp_cost_mult.get(elem, 0.0))
    if extra <= 0:
        return base
    # Round up so a 5 % multiplier on a 7-MP skill costs 8, not 7 — keeps
    # the player from squeezing a "free" pip out of fractional rounding.
    from math import ceil
    return int(ceil(base * (1.0 + extra)))


def _propagate_dot_bonuses(actor: Combatant, target: Combatant) -> None:
    """Record the attacker's DoT-amplification stats + scaling context in the
    target's per-source map, then recompute the live aggregate fields.

    Multiple attackers' amplifiers add together; re-applications from the same
    attacker do NOT compound (source map is keyed by ``actor.key``).
    """
    target.dot_bonus_sources[actor.key] = {
        "dot":            actor.dot_dmg_bonus,
        "burn":           float(actor.dot_dmg_bonus_by_kind.get("burn", 0.0)),
        "bleed":          float(actor.dot_dmg_bonus_by_kind.get("bleed", 0.0)),
        "poison":         float(actor.dot_dmg_bonus_by_kind.get("poison", 0.0)),
        "power":          max(actor.atk, actor.matk),
        # Recorded so caster-stat-driven DoTs (Lục Hồn Chú-class) can keep
        # ticking with the applier's HP pool + spell power even after the
        # caster has moved on to other actions.
        "caster_hp_max":  actor.hp_max,
        "caster_matk":    actor.matk,
        "scales_hp_pct":  actor.dot_scales_hp_pct,
    }
    target.dot_dmg_bonus = sum(s["dot"] for s in target.dot_bonus_sources.values())
    target.dot_dmg_bonus_by_kind = {
        kind: sum(s[kind] for s in target.dot_bonus_sources.values())
        for kind in ("burn", "bleed", "poison")
    }
    # Drop zero entries so the dict stays tidy (and `if dict.get(...)` short-
    # circuits remain meaningful).
    target.dot_dmg_bonus_by_kind = {
        k: v for k, v in target.dot_dmg_bonus_by_kind.items() if v
    }
    target.dot_scales_hp_pct = any(s["scales_hp_pct"] for s in target.dot_bonus_sources.values())


# Per-element field names carried from attacker to target on stack application.
# Max-merge keeps the strongest build in play when multiple attackers stack the
# same DoT on one target.
# All stack caps now live on ``EffectMeta.stack_cap`` and are resolved via
# ``effective_stack_cap`` (with gear/constitution/Linh Căn flat bonuses
# routed through ``combatant.stack_cap_bonuses``). This table propagates
# build flags (per-stack pct, bleed_heal_reduce, etc.) from attacker to
# target so DoT ticks honour the attacker's gear — caps are not in this
# table because they don't ride on Combatant fields anymore.
_STACK_BUILD_FIELDS: dict[str, tuple[str, ...]] = {
    "burn":       ("burn_per_stack_pct",),
    "bleed":      ("bleed_per_stack_pct", "bleed_heal_reduce"),
    "shock":      ("shock_per_stack_pct",),
    "poison":     ("poison_per_stack_pct",),
    "chan_hoa":   ("chan_hoa_per_stack_pct", "chan_hoa_per_stack_fire_amp"),
    "nghiep_hoa": ("nghiep_hoa_per_stack_pct",),
}


# Stack kind → owning EffectMeta key. Used by ``_propagate_stack_build`` and
# ``character_stats`` to seed Combatant fields from meta-declared defaults
# (``EffectMeta.stack_cap`` / ``per_stack_pct``). Shock isn't in
# ``_STACK_DOT_FIELDS`` (it amps incoming Lôi damage instead of ticking) but
# still uses this mapping so its cap/per-stack values can also be tuned in
# ``effects.py`` rather than ``balance.py`` constants.
_STACK_KIND_TO_EFFECT_KEY: dict[str, str] = {
    "burn":       "DebuffThieuDot",
    "bleed":      "DebuffChayMau",
    "shock":      "DebuffSocDien",
    "poison":     "DebuffDocTo",
    "chan_hoa":   "DebuffChanHoa",
    "nghiep_hoa": "DebuffNghiepHoa",
}


# Lazy cache populated from ``EFFECTS`` on first read — avoids the
# circular-import dance of grabbing it at module load time.
_META_BY_STACK_KIND_CACHE: dict[str, object] | None = None


def _meta_for_stack_kind(kind: str):
    """Return the EffectMeta owning ``kind`` (None if unknown)."""
    global _META_BY_STACK_KIND_CACHE
    if _META_BY_STACK_KIND_CACHE is None:
        from src.game.engine.effects import EFFECTS
        _META_BY_STACK_KIND_CACHE = {
            k: EFFECTS[ek] for k, ek in _STACK_KIND_TO_EFFECT_KEY.items() if ek in EFFECTS
        }
    return _META_BY_STACK_KIND_CACHE.get(kind)


def meta_stack_cap(kind: str) -> int:
    """Designer-tuned stack cap from the meta, or 0 if unset (caller falls back)."""
    meta = _meta_for_stack_kind(kind)
    return int(meta.stack_cap) if meta is not None else 0


def meta_per_stack_pct(kind: str) -> float:
    """Designer-tuned per-stack damage fraction from the meta, or 0.0 if unset."""
    meta = _meta_for_stack_kind(kind)
    return float(meta.per_stack_pct) if meta is not None else 0.0


def _propagate_stack_build(actor: Combatant, target: Combatant, kind: str) -> None:
    """Copy the attacker's DoT-stack build flags onto the target (max-merge).

    kind ∈ {"burn", "bleed", "shock", "poison", "chan_hoa", "nghiep_hoa"}.
    Fire/bleed/poison/chan_hoa/nghiep_hoa also propagate ``dot_can_crit`` and
    the DoT damage-bonus aggregate so the holder's DoT ticks honour the
    attacker's build. Shock only needs the cap + per-stack.

    Also seeds the holder's ``per_stack_pct`` from any
    ``EffectMeta.per_stack_pct`` declared for this kind — so designers can
    tune the per-stack damage from effects.py without touching Combatant
    field defaults. Max-merge so gear-uplifts on the actor or holder still
    win. Stack caps are NOT seeded here — they're resolved at read time via
    ``effective_stack_cap`` (meta + ``stack_cap_bonuses`` + buff bonuses).
    """
    for field_name in _STACK_BUILD_FIELDS[kind]:
        a_val = getattr(actor, field_name)
        if a_val > getattr(target, field_name):
            setattr(target, field_name, a_val)
    # Meta-driven per-stack-pct default — keep the holder's value at least
    # as high as the meta declares.
    meta = _meta_for_stack_kind(kind)
    if meta is not None:
        if meta.per_stack_pct > 0:
            pct_attr = f"{kind}_per_stack_pct"
            if meta.per_stack_pct > getattr(target, pct_attr, 0.0):
                setattr(target, pct_attr, meta.per_stack_pct)
    if kind in ("burn", "bleed", "poison", "chan_hoa", "nghiep_hoa"):
        if actor.dot_can_crit:
            target.dot_can_crit = True
        _propagate_dot_bonuses(actor, target)


def _build_skill_obj(
    skill_key: str, skill_data: dict, mp_cost: int,
    base_dmg_override: int | None = None,
):
    """Hydrate a runtime Skill dataclass from a registry dict entry.

    Kept local-import because the Skill model pulls in several enums that
    the combat module otherwise doesn't need.

    ``base_dmg_override`` lets the caller substitute a computed base damage
    (e.g. gem-threshold scaling on ``per_hit_followup`` formation skills)
    without mutating the registry dict.
    """
    from src.game.models.skill import AttackType, DmgScale, Skill, SkillCategory
    base_dmg = (
        int(base_dmg_override)
        if base_dmg_override is not None
        else skill_data.get("base_dmg", 0)
    )
    return Skill(
        key=skill_key,
        vi=skill_data.get("vi", ""),
        en=skill_data.get("en", ""),
        category=SkillCategory(skill_data.get("category", "attack")),
        mp_cost=mp_cost,
        cooldown=skill_data.get("cooldown", 1),
        base_dmg=base_dmg,
        element=skill_data.get("element"),
        attack_type=AttackType(skill_data.get("attack_type", "magical")),
        dmg_scale=DmgScale.from_raw(skill_data.get("dmg_scale")),
        bypass_evasion=bool(skill_data.get("bypass_evasion", False)),
    )


# On-hit proc table driven by procs.run_on_hit_procs. Each entry describes a
# single chance-based proc that runs after a successful damaging hit.
#   chance_attr: actor attribute name holding the proc probability (float 0-1)
#   effect_key : EffectKey applied to target on success
#   stack_kind : optional key into _STACK_BUILD_FIELDS — when set, the
#                attacker's build flags propagate and a stack is added
#                (stack_add_fn is looked up on Combatant by name).
#   stack_add  : Combatant method name that adds a stack (returns stacks gained)
#   stacks_attr: attribute name read from target to format the log. Stack
#                cap is computed via ``effective_stack_cap(target, effect_key)``.
#   log_fmt    : Vietnamese message template, positional or keyword placeholders
# Special cases (immune_hard_cc for stun, freeze_on_skill_chance for freeze)
# are kept as plain inline branches below the loop.
_ON_HIT_PROCS: tuple[dict, ...] = (
    {
        "chance_attr": "burn_on_hit_pct", "effect_key": EffectKey.DEBUFF_THIEU_DOT,
        "stack_kind":  "burn",  "stack_add": "add_burn_stack",
        "stacks_attr": "burn_stacks",
        "log_fmt": "    🔥 Thiêu Đốt kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "bleed_on_hit_pct", "effect_key": EffectKey.DEBUFF_CHAY_MAU,
        "stack_kind":  "bleed", "stack_add": "add_bleed_stack",
        "stacks_attr": "bleed_stacks",
        "log_fmt": "    🩸 Chảy Máu kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "shock_on_hit_pct", "effect_key": EffectKey.DEBUFF_SOC_DIEN,
        "stack_kind":  "shock", "stack_add": "add_shock_stack",
        "stacks_attr": "shock_stacks",
        "log_fmt": "    ⚡ Sốc Điện kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "mark_on_hit_pct", "effect_key": EffectKey.DEBUFF_AN_PHONG,
        "log_fmt": "    🌀 Ấn Phong kích hoạt!",
    },
    {
        "chance_attr": "slow_on_hit_pct", "effect_key": EffectKey.DEBUFF_LAM_CHAM,
        "log_fmt": "    🐢 Làm Chậm kích hoạt!",
    },
    {
        "chance_attr": "heal_reduce_on_hit_pct", "effect_key": EffectKey.DEBUFF_CAT_DUT,
        "log_fmt": "    ✂️ Cắt Đứt kích hoạt!",
    },
    {
        "chance_attr": "blind_on_hit_pct", "effect_key": EffectKey.DEBUFF_LOA_MAT,
        "log_fmt": "    🌫️ Lóa Mắt kích hoạt!",
    },
)


def effective_spd(combatant: Combatant) -> int:
    """Live SPD after applying active spd_pct modifiers (BuffTangToc, DebuffLamCham…)."""
    mods = get_combat_modifiers(combatant)
    return max(1, round(combatant.spd * (1.0 + mods.get("spd_pct", 0.0))))


def spd_extra_turn_pct(actor_spd: int, target_spd: int) -> float:
    """Fractional chance that the actor takes a bonus action after its turn.

    Returns 0 when actor is not faster than target. Scales with relative SPD gap
    and is clamped at SPD_EXTRA_TURN_MAX_PCT so SPD alone never fully locks out
    the slower side.
    """
    if actor_spd <= target_spd:
        return 0.0
    gap_pct = (actor_spd - target_spd) / max(1, target_spd)
    return min(SPD_EXTRA_TURN_MAX_PCT, gap_pct * SPD_EXTRA_TURN_SCALE)
