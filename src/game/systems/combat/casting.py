"""Skill-cast pipeline.

Covers the path a single skill takes from "actor selected it" to "target took
the hit": damage calculation, effect application, support-skill buffs, and the
parallel formation barrage that multi-slot Trận Tu relies on.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.data.registry import registry
from src.game.constants.balance import (
    TRUE_DMG_PCT_CAP, TRUE_DMG_WORLD_BOSS_FLOOR_PCT, TRUE_DMG_WORLD_BOSS_MULT,
)
from src.game.constants.effects import EffectKey
from src.game.engine import linh_can_effects as lc_effects
from src.game.engine.damage import (
    apply_damage_scaling, build_attack_stats, build_defense_stats,
    calculate_damage, colorize_damage, effective_damage_reduction,
    spd_evasion_bonus,
)
from src.game.engine.effects import (
    EFFECTS, EffectMeta, default_duration, get_combat_modifiers,
)
from src.game.systems.combatant import Combatant

from .bursts import burst_burn, burst_mana_stacks, burst_shield
from .helpers import _build_skill_obj, _propagate_dot_bonuses, _propagate_stack_build
from .procs import apply_reactive_damage, apply_soul_drain, apply_stat_steal, run_on_hit_procs

if TYPE_CHECKING:
    from .session import CombatSession


def cast_skill(
    session: "CombatSession", actor: Combatant, target: Combatant,
    skill_key: str, skill_data: dict, mp_cost: int,
) -> None:
    """Execute one skill cast — MP spend, damage pipeline or support
    effects, on-hit procs, and cooldown. Extracted so the main rotation
    and parallel formation firing share the same machinery."""
    actor.mp = max(0, actor.mp - mp_cost)
    base_dmg = skill_data.get("base_dmg", 0)

    actor_mods = get_combat_modifiers(actor)
    target_mods = get_combat_modifiers(target)

    if base_dmg > 0:
        skill_obj = _build_skill_obj(skill_key, skill_data, mp_cost)
        attack_stats = build_attack_stats(actor, target, actor_mods, skill_obj.element)
        defense_stats = build_defense_stats(target, target_mods, actor, spd_evasion_bonus)
        # Pre-damage: Kim Linh Căn — may gain elemental penetration this hit
        pen_pct = lc_effects.get_pen_pct(actor, session.rng, session.log)
        result = calculate_damage(skill_obj, attack_stats, defense_stats, session.rng, pen_pct)
        dmg = result.final
        crit_tag = " 💥BẠO KÍCH!" if result.is_crit else ""

        if result.is_evaded:
            session.log.append(
                f"  🌀 **{actor.name}** dùng *{skill_data['vi']}* → **{target.name}** né tránh!"
            )
        else:
            # Target damage reduction → actor's HP/MP/evasion/shield/mana-stack scaling
            target_dr = effective_damage_reduction(target, target_mods)
            if target_dr > 0:
                dmg = int(dmg * (1.0 - target_dr))
            dmg = apply_damage_scaling(dmg, actor, actor_mods)

            # Moc build: consume queued heal→damage built up from prior heals
            if actor.queued_heal_dmg > 0:
                dmg += actor.queued_heal_dmg
                session.log.append(
                    f"    🌿 Dưỡng Sinh Hóa Sát — +{actor.queued_heal_dmg:,} ST từ máu hồi"
                )
                actor.queued_heal_dmg = 0

            # Apply Thổ shield (absorbs before HP loss)
            if target.shield > 0:
                absorbed = min(target.shield, dmg)
                target.shield -= absorbed
                dmg -= absorbed
                if absorbed > 0:
                    session.log.append(f"    🛡️ Khiên [Thổ] hấp thụ {absorbed:,} sát thương!")

            # BuffBatTu — prevent killing blow once
            if dmg >= target.hp and target.has_effect(EffectKey.BUFF_BAT_TU):
                dmg = target.hp - 1
                target.effects.pop(EffectKey.BUFF_BAT_TU, None)
                session.log.append(f"    💫 **{target.name}** kích hoạt **Bất Tử** — sống sót!")

            target.hp = max(0, target.hp - dmg)
            skill_elem = skill_data.get("element")
            dmg_tag = colorize_damage(f"-{dmg:,} HP", skill_elem)
            session.log.append(
                f"  ⚡ **{actor.name}** dùng *{skill_data['vi']}* → {dmg_tag}{crit_tag}"
                f" | {target.name}: {target.hp:,}/{target.hp_max:,} HP"
            )

            skill_true_pct = float(skill_data.get("true_dmg_pct", 0.0))
            total_true_pct = min(
                TRUE_DMG_PCT_CAP, skill_true_pct + actor.true_dmg_pct,
            )
            if target.is_world_boss:
                total_true_pct = min(
                    TRUE_DMG_WORLD_BOSS_FLOOR_PCT,
                    total_true_pct * TRUE_DMG_WORLD_BOSS_MULT,
                )
            if target.is_alive() and total_true_pct > 0:
                true_dmg = max(1, int(target.hp_max * total_true_pct))
                if result.is_crit:
                    true_dmg = int(true_dmg * 1.5)
                target.hp = max(0, target.hp - true_dmg)
                true_tag = colorize_damage(f"-{true_dmg:,} HP", None, true_dmg=True)
                session.log.append(
                    f"    🗡️ **Chân Thương** xuyên mọi phòng ngự → {true_tag}"
                    f" ({total_true_pct * 100:.1f}% máu)"
                )

            run_on_hit_procs(session, actor, target, is_crit=result.is_crit)
            session._apply_mana_gains(actor, dmg)
            apply_reactive_damage(session, actor, target, dmg)

            # On-hit: Linh Căn procs (consolidated in effects.py)
            lc_effects.on_hit(actor, target, dmg, result.is_crit, session.rng, session.log)

        # Apply skill debuff/CC effects to target
        apply_skill_effects(session, skill_data, actor, target, hit=not result.is_evaded)

    else:
        # Support / defense skill — applies effects to self (actor)
        session.log.append(f"  🛡️ **{actor.name}** dùng *{skill_data['vi']}*")
        apply_support_skill(session, skill_data, actor, target)

    actor.set_cooldown(skill_key, skill_data.get("cooldown", 1))


def fire_formation_skills(
    session: "CombatSession", actor: Combatant, target: Combatant
) -> None:
    """Parallel formation barrage — after the main cast, each active
    formation's signature skill auto-fires if it's off cooldown and the
    actor can afford the MP. Unlike the main rotation, a formation skill
    that can't afford MP simply sits out (no auto-attack fallback).
    Insufficient-cooldown and MP guards are silent to keep the log clean.
    """
    if not actor.formation_skill_keys:
        return
    for frm_key in actor.formation_skill_keys:
        if not target.is_alive():
            break
        if actor.skill_on_cooldown(frm_key):
            continue
        skill_data = registry.get_skill(frm_key)
        if not skill_data:
            continue
        mp_cost = skill_data.get("mp_cost", 999)
        if actor.mp < mp_cost:
            continue
        session.log.append(
            f"  🔯 **{actor.name}** · **{skill_data.get('vi', frm_key)}** (trận song phát):"
        )
        cast_skill(session, actor, target, frm_key, skill_data, mp_cost)


def apply_support_skill(
    session: "CombatSession", skill_data: dict, actor: Combatant, target: Combatant
) -> None:
    """Handle a support/defense skill: instant heals and buff application."""
    effect_chances: dict[str, float] = skill_data.get("effect_chances", {})
    effect_overrides: dict[str, dict] = skill_data.get("effect_overrides", {})
    for effect_key in skill_data.get("effects", []):
        meta = EFFECTS.get(effect_key)

        if effect_key == "HpRegen":
            # Instant HP heal (10% of max) — routed through _apply_heal so
            # bleed reduction, clamp, and queued_heal_dmg all apply.
            actor_mods = get_combat_modifiers(actor)
            heal_mult = 1.0 + actor.heal_pct + actor_mods.get("hp_regen_pct", 0.0)
            requested = max(1, int(actor.hp_max * 0.10 * heal_mult))
            if actor.bleed_stacks > 0 and actor.bleed_heal_reduce > 0:
                session.log.append(
                    f"    🩸 *Chảy Máu giảm hiệu lực hồi máu {actor.bleed_heal_reduce * 100:.0f}%*"
                )
            applied = session._apply_heal(actor, requested)
            session.log.append(f"    ❤️ +{applied:,} HP")

        elif effect_key == "MpRegen":
            # Instant MP restore (10% of max)
            regen = max(1, actor.mp_max // 10)
            actor.mp = min(actor.mp_max, actor.mp + regen)
            session.log.append(f"    💙 +{regen:,} MP")

        elif meta and meta.kind.value == "buff":
            # Apply buff to self (actor) — overrides may carry custom duration
            # or stronger stat_bonus values.
            override = effect_overrides.get(effect_key)
            dur = int((override or {}).get("duration", default_duration(effect_key)))
            stamp = {k: v for k, v in (override or {}).items() if k != "duration"} or None
            actor.apply_effect(effect_key, dur, overrides=stamp)
            session.log.append(
                f"    {meta.emoji} **{meta.vi}** ({dur}t) — {meta.description_vi}"
            )

        elif meta and meta.kind.value in ("debuff", "cc"):
            # CC skills with base_dmg=0 that debuff the target (e.g. CCBind skill)
            base_chance = effect_chances.get(effect_key, 1.0)
            effective_chance = base_chance * (1.0 - target.debuff_immune_pct)
            if effective_chance >= 1.0 or session.rng.random() < effective_chance:
                inflict_debuff(
                    session, effect_key, meta, target, actor=actor,
                    overrides=effect_overrides.get(effect_key),
                )


def apply_skill_effects(
    session: "CombatSession", skill_data: dict,
    actor: Combatant, target: Combatant, hit: bool,
) -> None:
    """Apply all effect_keys from a skill's effects list to the appropriate target.

    For debuffs/CC: checks the skill's effect_chances dict (default 1.0) multiplied
    by (1 - target.debuff_immune_pct) to get the effective proc probability.

    Special keywords handled in-line (not in the EFFECTS registry):
      - ``ConsumeBurnBurst``: detonate all burn stacks on the target for
        burst damage proportional to stack count.
    """
    effect_chances: dict[str, float] = skill_data.get("effect_chances", {})
    effect_overrides: dict[str, dict] = skill_data.get("effect_overrides", {})
    for effect_key in skill_data.get("effects", []):
        # ── Special: burn burst ───────────────────────────────────────────
        if effect_key == "ConsumeBurnBurst":
            if hit and target.burn_stacks > 0:
                burst_burn(session, actor, target, skill_data)
            continue
        # ── Special: Thủy mana-stack burst ────────────────────────────────
        if effect_key == "ConsumeManaBurst":
            if hit and actor.mana_stacks > 0:
                burst_mana_stacks(session, actor, target, skill_data)
            continue
        # ── Special: Thổ shield burst ────────────────────────────────────
        if effect_key == "ConsumeShieldBurst":
            if hit and actor.shield > 0:
                burst_shield(session, actor, target, skill_data)
            continue
        # ── Special: Âm soul-drain on skill hit ──────────────────────────
        # The skill may specify ``soul_drain_procs`` for multi-proc bursts;
        # default 1 mirrors the on-hit passive.
        if effect_key == "ApplySoulDrain":
            if hit:
                for _ in range(max(1, int(skill_data.get("soul_drain_procs", 1)))):
                    apply_soul_drain(session, actor, target)
            continue
        # ── Special: Âm stat-steal on skill hit ──────────────────────────
        if effect_key == "ApplyStatSteal":
            if hit:
                for _ in range(max(1, int(skill_data.get("stat_steal_procs", 1)))):
                    apply_stat_steal(session, actor, target)
            continue

        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        override = effect_overrides.get(effect_key)
        if meta.kind.value == "buff":
            # Self-buff (actor), e.g. a skill that deals damage AND grants a buff.
            # Override may carry a custom duration or stronger stat_bonus.
            dur = int((override or {}).get("duration", default_duration(effect_key)))
            stamp = {k: v for k, v in (override or {}).items() if k != "duration"} or None
            actor.apply_effect(effect_key, dur, overrides=stamp)
            session.log.append(f"    {meta.emoji} **{actor.name}** nhận **{meta.vi}** ({dur}t)")
        elif hit and meta.kind.value in ("debuff", "cc"):
            base_chance = effect_chances.get(effect_key, 1.0)
            effective_chance = base_chance * (1.0 - target.debuff_immune_pct)
            if effective_chance >= 1.0 or session.rng.random() < effective_chance:
                inflict_debuff(
                    session, effect_key, meta, target, actor=actor,
                    overrides=override,
                )


def inflict_debuff(
    session: "CombatSession", effect_key: str, meta: EffectMeta, target: Combatant,
    actor: Combatant | None = None,
    overrides: dict | None = None,
) -> None:
    """Apply a debuff or CC to target, respecting immunities.

    ``actor`` is optional; when provided, fire-build flags (dot_can_crit,
    burn_per_stack_pct) are propagated to the target so DoT ticks honor
    the attacker's build.
    ``overrides`` is the per-instance magnitude override dict from the
    skill's ``effect_overrides[<key>]`` entry. May contain a ``duration``
    field to lengthen the effect beyond the meta default; everything else
    (stat_bonus, dot_pct, dot_element) is forwarded to ``apply_effect``.
    """
    if effect_key == EffectKey.DEBUFF_DOC_TO and target.poison_immunity:
        session.log.append(f"    💚 **{target.name}** miễn dịch Độc Tố!")
        return
    # World bosses / immune_hard_cc combatants shrug off hard CC (stun,
    # freeze, silence, interrupt, knock-up). Soft debuffs still apply.
    if target.immune_hard_cc and (meta.skips_turn or meta.prevents_skills):
        session.log.append(
            f"    🛡️ **{target.name}** miễn dịch khống chế — **{meta.vi}** vô hiệu!"
        )
        return
    dur = int((overrides or {}).get("duration", default_duration(effect_key)))
    # Strip duration from the dict before stamping — it's a turn count, not
    # an effect-magnitude field, and apply_effect already takes it as arg.
    stamp = {k: v for k, v in (overrides or {}).items() if k != "duration"} or None
    target.apply_effect(effect_key, dur, overrides=stamp)
    # Burn stacks on every application — fire-build cornerstone
    if effect_key == EffectKey.DEBUFF_THIEU_DOT:
        if actor is not None:
            _propagate_stack_build(actor, target, "burn")
        target.add_burn_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** [×{target.burn_stacks}/{target.burn_stack_cap}] ({dur}t)"
        )
        return
    # Bleed stacks — Kim playstyle mirror of burn
    if effect_key == EffectKey.DEBUFF_CHAY_MAU:
        if actor is not None:
            _propagate_stack_build(actor, target, "bleed")
        target.add_bleed_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** [×{target.bleed_stacks}/{target.bleed_stack_cap}] ({dur}t)"
        )
        return
    # Shock stacks — Lôi playstyle mirror of burn
    if effect_key == EffectKey.DEBUFF_SOC_DIEN:
        if actor is not None:
            _propagate_stack_build(actor, target, "shock")
        target.add_shock_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** [×{target.shock_stacks}/{target.shock_stack_cap}] ({dur}t)"
        )
        return
    # Generic DoT: propagate attacker's DoT damage boosters (poison, bleed-mk2, etc.)
    if meta.dot_pct > 0 and actor is not None:
        _propagate_dot_bonuses(actor, target)
    session.log.append(
        f"    {meta.emoji} **{target.name}** bị **{meta.vi}** ({dur}t)"
    )


def auto_attack(
    session: "CombatSession", actor: Combatant, target: Combatant
) -> None:
    """Physical auto-attack using ATK stat, reduced by target physical defense."""
    from src.game.engine.damage.physical import apply_physical_defense
    raw = max(1, int(actor.atk * session.rng.uniform(0.85, 1.15) + 5))
    dmg = apply_physical_defense(raw, "physical", target.def_stat)
    if target.final_dmg_reduce > 0:
        dmg = int(dmg * (1.0 - target.final_dmg_reduce))
    dmg = max(1, dmg)
    target.hp = max(0, target.hp - dmg)
    session.log.append(
        f"  👊 **{actor.name}** tấn công cơ bản → "
        f"{colorize_damage(f'-{dmg} HP', None)}"
    )
