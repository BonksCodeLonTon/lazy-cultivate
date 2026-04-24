"""Module-level helpers used across the combat package.

Contains only stateless utilities: no session reference, no Discord IO.
Keeping them here breaks the import cycle between session/casting/procs.
"""
from __future__ import annotations

from src.game.constants.balance import SPD_EXTRA_TURN_MAX_PCT, SPD_EXTRA_TURN_SCALE
from src.game.constants.effects import EffectKey
from src.game.engine.effects import get_combat_modifiers
from src.game.systems.combatant import Combatant


def _propagate_dot_bonuses(actor: Combatant, target: Combatant) -> None:
    """Record the attacker's DoT-amplification stats + scaling context in the
    target's per-source map, then recompute the live aggregate fields.

    Multiple attackers' amplifiers add together; re-applications from the same
    attacker do NOT compound (source map is keyed by ``actor.key``).
    """
    target.dot_bonus_sources[actor.key] = {
        "dot":            actor.dot_dmg_bonus,
        "burn":           actor.burn_dmg_bonus,
        "bleed":          actor.bleed_dmg_bonus,
        "poison":         actor.poison_dmg_bonus,
        "power":          max(actor.atk, actor.matk),
        "scales_hp_pct":  actor.dot_scales_hp_pct,
    }
    target.dot_dmg_bonus    = sum(s["dot"]    for s in target.dot_bonus_sources.values())
    target.burn_dmg_bonus   = sum(s["burn"]   for s in target.dot_bonus_sources.values())
    target.bleed_dmg_bonus  = sum(s["bleed"]  for s in target.dot_bonus_sources.values())
    target.poison_dmg_bonus = sum(s["poison"] for s in target.dot_bonus_sources.values())
    target.dot_scales_hp_pct = any(s["scales_hp_pct"] for s in target.dot_bonus_sources.values())


# Per-element field names carried from attacker to target on stack application.
# Max-merge keeps the strongest build in play when multiple attackers stack the
# same DoT on one target.
_STACK_BUILD_FIELDS: dict[str, tuple[str, ...]] = {
    "burn":  ("burn_stack_cap", "burn_per_stack_pct"),
    "bleed": ("bleed_stack_cap", "bleed_per_stack_pct", "bleed_heal_reduce"),
    "shock": ("shock_stack_cap", "shock_per_stack_pct"),
}


def _propagate_stack_build(actor: Combatant, target: Combatant, kind: str) -> None:
    """Copy the attacker's DoT-stack build flags onto the target (max-merge).

    kind ∈ {"burn", "bleed", "shock"}. Fire/bleed also propagate ``dot_can_crit``
    and the DoT damage-bonus aggregate. Shock only needs the cap + per-stack.
    """
    for field_name in _STACK_BUILD_FIELDS[kind]:
        a_val = getattr(actor, field_name)
        if a_val > getattr(target, field_name):
            setattr(target, field_name, a_val)
    if kind in ("burn", "bleed"):
        if actor.dot_can_crit:
            target.dot_can_crit = True
        _propagate_dot_bonuses(actor, target)


def _build_skill_obj(skill_key: str, skill_data: dict, mp_cost: int):
    """Hydrate a runtime Skill dataclass from a registry dict entry.

    Kept local-import because the Skill model pulls in several enums that
    the combat module otherwise doesn't need.
    """
    from src.game.models.skill import AttackType, DmgScale, Skill, SkillCategory
    return Skill(
        key=skill_key,
        vi=skill_data.get("vi", ""),
        en=skill_data.get("en", ""),
        realm=int(skill_data.get("realm", 1)),
        category=SkillCategory(skill_data.get("category", "attack")),
        mp_cost=mp_cost,
        cooldown=skill_data.get("cooldown", 1),
        base_dmg=skill_data.get("base_dmg", 0),
        element=skill_data.get("element"),
        attack_type=AttackType(skill_data.get("attack_type", "magical")),
        dmg_scale=DmgScale.from_raw(skill_data.get("dmg_scale")),
    )


# On-hit proc table driven by procs.run_on_hit_procs. Each entry describes a
# single chance-based proc that runs after a successful damaging hit.
#   chance_attr: actor attribute name holding the proc probability (float 0-1)
#   effect_key : EffectKey applied to target on success
#   stack_kind : optional key into _STACK_BUILD_FIELDS — when set, the
#                attacker's build flags propagate and a stack is added
#                (stack_add_fn is looked up on Combatant by name).
#   stack_add  : Combatant method name that adds a stack (returns stacks gained)
#   stacks_attr/cap_attr: attribute names read from target to format the log
#   log_fmt    : Vietnamese message template, positional or keyword placeholders
# Special cases (immune_hard_cc for stun, fixed 0.15 chance for freeze_on_skill)
# are kept as plain inline branches below the loop.
_ON_HIT_PROCS: tuple[dict, ...] = (
    {
        "chance_attr": "burn_on_hit_pct", "effect_key": EffectKey.DEBUFF_THIEU_DOT,
        "stack_kind":  "burn",  "stack_add": "add_burn_stack",
        "stacks_attr": "burn_stacks",  "cap_attr": "burn_stack_cap",
        "log_fmt": "    🔥 Thiêu Đốt kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "bleed_on_hit_pct", "effect_key": EffectKey.DEBUFF_CHAY_MAU,
        "stack_kind":  "bleed", "stack_add": "add_bleed_stack",
        "stacks_attr": "bleed_stacks", "cap_attr": "bleed_stack_cap",
        "log_fmt": "    🩸 Chảy Máu kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "shock_on_hit_pct", "effect_key": EffectKey.DEBUFF_SOC_DIEN,
        "stack_kind":  "shock", "stack_add": "add_shock_stack",
        "stacks_attr": "shock_stacks", "cap_attr": "shock_stack_cap",
        "log_fmt": "    ⚡ Sốc Điện kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "mark_on_hit_pct", "effect_key": EffectKey.DEBUFF_PHONG_AN,
        "log_fmt": "    🌀 Phong Ấn kích hoạt!",
    },
    {
        "chance_attr": "slow_on_hit_pct", "effect_key": EffectKey.DEBUFF_LAM_CHAM,
        "log_fmt": "    🐢 Làm Chậm kích hoạt!",
    },
    {
        "chance_attr": "heal_reduce_on_hit_pct", "effect_key": EffectKey.DEBUFF_CAT_DUT,
        "log_fmt": "    ✂️ Cắt Đứt kích hoạt!",
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
