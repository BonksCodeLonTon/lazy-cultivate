"""Skill-cast pipeline.

Covers the path a single skill takes from "actor selected it" to "target took
the hit": damage calculation, effect application, support-skill buffs, and the
parallel formation barrage that multi-slot Trận Tu relies on.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.data.registry import registry
from src.game.constants.effects import EffectKey
from src.game.engine import linh_can_effects as lc_effects
from src.game.engine.damage import (
    apply_damage_scaling, build_attack_stats, build_defense_stats,
    calculate_damage, colorize_damage, effective_damage_reduction,
    spd_evasion_bonus,
)
from src.game.engine.damage.true_damage import apply_true_damage
from src.game.engine.effects import (
    EFFECTS, EffectKind, EffectMeta, check_attack_miss, default_duration,
    effective_stack_cap, get_combat_modifiers,
)
from src.game.systems.combatant import Combatant

from .bursts import burst_mana_stacks, burst_shield
from .helpers import _build_skill_obj, _propagate_dot_bonuses, _propagate_stack_build
from .procs import (
    apply_buff_steal, apply_life_steal, apply_reactive_damage, apply_soul_drain,
    apply_stat_steal, run_on_hit_procs,
)
from .skill_extras import (
    apply_charge_bonus, cast_chain_skill, consume_auto_cast_stacks, maybe_spawn_summon,
)

if TYPE_CHECKING:
    from .session import CombatSession


def _effective_debuff_chance(
    base_chance: float, actor: Combatant, target: Combatant,
) -> float:
    """Combine the skill's base chance with the actor's apply-bonus and the
    target's immunity, returning the rolled probability.

    Active-effect modifiers fold in via ``get_combat_modifiers``:
      • ``debuff_apply_bonus`` on the actor adds flat to the base chance
      • ``debuff_immune_pct`` on the target adds to its base immunity
    Both are clamped before the final multiply so a stacked buff can't push
    the roll past 100% before the immunity multiply, and a stacked immunity
    debuff can't go below 0.
    """
    actor_bonus = float(get_combat_modifiers(actor).get("debuff_apply_bonus", 0.0))
    immune_mod = float(get_combat_modifiers(target).get("debuff_immune_pct", 0.0))
    boosted = max(0.0, min(1.0, base_chance + actor_bonus))
    immune = max(0.0, min(1.0, target.debuff_immune_pct + immune_mod))
    return boosted * (1.0 - immune)


_LUU_QUANG_STEP = 0.10
_LUU_QUANG_CAP = 0.30


def _bump_luu_quang_stack(
    session: "CombatSession", target: Combatant, dmg: int,
) -> None:
    """Ramp Lưu Quang Huyễn Ảnh's ``spd_pct`` override one step toward the
    +30% cap. No-op when the target lacks the buff or the hit dealt zero
    damage. Preserves the buff's remaining duration — no refresh on bump.
    """
    if dmg <= 0 or not target.has_effect(EffectKey.BUFF_LUU_QUANG_HUYEN_ANH):
        return
    override = target.effect_overrides.get(EffectKey.BUFF_LUU_QUANG_HUYEN_ANH, {})
    cur_spd = float((override.get("stat_bonus") or {}).get("spd_pct", 0.0))
    next_spd = min(_LUU_QUANG_CAP, cur_spd + _LUU_QUANG_STEP)
    if next_spd <= cur_spd:
        return
    cur_dur = target.effects.get(EffectKey.BUFF_LUU_QUANG_HUYEN_ANH, 0)
    target.apply_effect(
        EffectKey.BUFF_LUU_QUANG_HUYEN_ANH,
        cur_dur,
        overrides={"stat_bonus": {"spd_pct": next_spd}},
    )
    session.log.append(
        f"    ✨ **{target.name}** Lưu Quang Huyễn Ảnh "
        f"[+{int(next_spd * 100)}% TĐ]"
    )


def cast_skill(
    session: "CombatSession", actor: Combatant, target: Combatant,
    skill_key: str, skill_data: dict, mp_cost: int,
    _suppress_extras: bool = False,
) -> None:
    """Execute one skill cast — MP spend, damage pipeline or support
    effects, on-hit procs, and cooldown. Extracted so the main rotation
    and parallel formation firing share the same machinery.

    ``_suppress_extras`` is set internally for multi-hit follow-ups and
    chained casts so per-cast hooks (cooldown, charge counter, chain skill,
    summon spawn) only fire once per logical cast.
    """
    actor.mp = max(0, actor.mp - mp_cost)
    base_dmg = skill_data.get("base_dmg", 0)
    # Per-formation gem scaling on passive echo skills — only the matching
    # ``per_hit_followup`` skill picks up its formation's threshold-fed
    # damage bonus, so standard skill casts never see the multiplier.
    if base_dmg > 0 and skill_data.get("per_hit_followup"):
        bonus = float(getattr(actor, "formation_skill_dmg_bonus", 0.0) or 0.0)
        if bonus > 0:
            base_dmg = max(1, int(base_dmg * (1.0 + bonus)))
    dealt_total = 0
    # Cast context for the generic chain-skill cast_on triggers (crit / kill
    # / shield_break). Filled inside the damage block below; consumed by
    # ``cast_chain_skill`` at the bottom. Stays empty for support / 0-dmg
    # casts so triggers like ``crit`` / ``kill`` correctly never fire.
    cast_ctx: dict = {"is_crit": False, "killed": False, "shield_broken": False}

    actor_mods = get_combat_modifiers(actor)
    target_mods = get_combat_modifiers(target)

    # Anti-regen riders — Vô Đạo (and any future skill that punishes a target
    # for healing). Each per-turn HP recovery on the target adds bonus base
    # damage at cast time:
    #   - bonus_dmg_per_target_regen_flat × target.hp_regen_flat
    #   - bonus_dmg_per_target_regen_pct  × (target.hp_max × target.hp_regen_pct)
    # Skipped when both scalars are zero so vanilla skills pay no overhead.
    flat_mult = float(skill_data.get("bonus_dmg_per_target_regen_flat", 0.0))
    pct_mult  = float(skill_data.get("bonus_dmg_per_target_regen_pct", 0.0))
    if (flat_mult or pct_mult) and base_dmg > 0:
        regen_flat_per_turn = max(0, int(getattr(target, "hp_regen_flat", 0)))
        regen_pct_per_turn = max(0, int(target.hp_max * float(getattr(target, "hp_regen_pct", 0.0))))
        regen_bonus = int(flat_mult * regen_flat_per_turn + pct_mult * regen_pct_per_turn)
        if regen_bonus > 0:
            base_dmg += regen_bonus
            skill_data = {**skill_data, "base_dmg": base_dmg}
            session.log.append(
                f"    🩸 *{skill_data['vi']}* khắc chế hồi sinh — "
                f"+{regen_bonus:,} ST cộng dồn"
            )

    if base_dmg > 0:
        # Blind miss check (Âm DebuffLoaMat) — happens before evasion roll
        # so a blinded attacker can whiff regardless of target's evasion.
        # MP is already spent above; matches DebuffTeLiet's "wasted swing" feel.
        if check_attack_miss(actor, session.rng):
            session.log.append(
                f"  🌫️ **{actor.name}** dùng *{skill_data['vi']}* → đánh trượt do **Lóa Mắt**!"
            )
            apply_skill_effects(session, skill_data, actor, target, hit=False)
            actor.set_cooldown(skill_key, skill_data.get("cooldown", 1))
            return

        # Cast-time per-target-buff bonus (Thẩm Phán Chi Nộ).
        # Counts only ``EffectKind.BUFF`` entries on the target. Folds into
        # actor_mods so build_attack_stats picks it up via final_dmg_bonus
        # for THIS cast only — punishes buff-stacked targets.
        per_buff_pct = float(skill_data.get("dmg_per_target_buff_pct", 0.0))
        if per_buff_pct > 0:
            buff_n = sum(
                1 for k in target.effects
                if (m := EFFECTS.get(k)) and m.kind is EffectKind.BUFF
            )
            if buff_n > 0:
                actor_mods["final_dmg_bonus"] = (
                    actor_mods.get("final_dmg_bonus", 0.0)
                    + per_buff_pct * buff_n
                )
                session.log.append(
                    f"    ⚖️ *Thẩm Phán* — +{per_buff_pct * buff_n * 100:.0f}% ST "
                    f"(×{buff_n} trạng thái tốt)"
                )

        # Per-skill self-penetration — Thủy Long Ngâm style "this cast carries
        # +X% pen of its own element". Folds into ``actor.element_pen[elem]``
        # only for the duration of the damage roll so other readers (linh_can
        # pulses, follow-up procs) see the original value. Subtracts from the
        # target's effective elemental res inside ``build_defense_stats``,
        # matching how formation / linh_can pen sources stack.
        skill_pen_self = float(skill_data.get("element_pen_self", 0.0))
        skill_elem = skill_data.get("element")
        prev_pen: float | None = None
        if skill_pen_self > 0 and skill_elem:
            prev_pen = actor.element_pen.get(skill_elem, 0.0)
            actor.element_pen[skill_elem] = prev_pen + skill_pen_self

        # Passive pen bonus — Dung Linh Chân Quyết-style threshold pen.
        # Scans the actor's owned passives for ``passive_pen_at_elem_dot_count``
        # entries whose element matches the current skill's element AND whose
        # opponent-DoT-count threshold is met (inclusive ``count >= threshold``);
        # sum the pen contributions additively. Folds into the same temporary
        # ``actor.element_pen`` slot so the ``finally`` restore at the end of
        # the try block clears both sources cleanly.
        if skill_elem:
            from src.game.engine.effects import count_elemental_dots
            passive_pen_total = 0.0
            for _sk in actor.skill_keys:
                _pdata = registry.get_skill(_sk)
                if not _pdata:
                    continue
                _spec = _pdata.get("passive_pen_at_elem_dot_count")
                if not _spec or _spec.get("element") != skill_elem:
                    continue
                _threshold = int(_spec.get("count", 0))
                _count = count_elemental_dots(target, skill_elem)
                if _count >= _threshold:
                    passive_pen_total += float(_spec.get("pen", 0.0))
            if passive_pen_total > 0:
                if prev_pen is None:
                    prev_pen = actor.element_pen.get(skill_elem, 0.0)
                actor.element_pen[skill_elem] = (
                    actor.element_pen.get(skill_elem, 0.0) + passive_pen_total
                )

        # Generic stat-diff bonus folding — any active effect carrying a
        # ``<output>_per_<source>_diff`` stat_bonus (e.g. Hải Thị Thận Lâu's
        # ``evasion_per_spd_diff: 20.0``) gets the gap-times-magnitude
        # injected into the matching standard stat key. Both sides are
        # processed so a future "atk-diff amps outgoing dmg" buff on the
        # actor works through the same helper. Reads live post-buff
        # effective values so synergy stacks (e.g. active spd_pct buff
        # widens the spd gap that the passive scales off).
        from src.game.engine.stat_diff import apply_stat_diff_bonuses
        apply_stat_diff_bonuses(target, target_mods, actor, actor_mods)
        apply_stat_diff_bonuses(actor, actor_mods, target, target_mods)

        # Per-target-stack scaling — Hỏa Vân Sậu Thiên-style. Read the
        # target's named stack count and apply per-stack bonuses to this
        # cast: ``crit_dmg_rating`` (additive int on actor), ``element_pen``
        # (additive float on actor's matching element). ``dmg_pct`` applies
        # post-result as a final-damage multiplier (further down). All
        # three are restored / one-shot so they only affect this cast.
        target_stack_scale = skill_data.get("scaling_per_target_stack")
        target_stack_count = 0
        prev_crit_dmg_rating: int | None = None
        if target_stack_scale:
            from .skill_extras import _STACK_FIELD
            _sk_kind = target_stack_scale.get("stack")
            _sk_field = _STACK_FIELD.get(_sk_kind) if _sk_kind else None
            if _sk_field:
                target_stack_count = int(getattr(target, _sk_field, 0))
            if target_stack_count > 0:
                _per_cd = int(target_stack_scale.get("crit_dmg_rating", 0))
                if _per_cd:
                    prev_crit_dmg_rating = actor.crit_dmg_rating
                    actor.crit_dmg_rating = prev_crit_dmg_rating + _per_cd * target_stack_count
                _per_pen = float(target_stack_scale.get("element_pen", 0.0))
                if _per_pen and skill_elem:
                    if prev_pen is None:
                        prev_pen = actor.element_pen.get(skill_elem, 0.0)
                    actor.element_pen[skill_elem] = (
                        actor.element_pen.get(skill_elem, 0.0)
                        + _per_pen * target_stack_count
                    )

        try:
            skill_obj = _build_skill_obj(skill_key, skill_data, mp_cost, base_dmg_override=base_dmg)
            attack_stats = build_attack_stats(actor, target, actor_mods, skill_obj.element)
            # Per-skill ``force_crit: true`` — always crit (e.g. Hỏa Vân
            # Sậu Thiên Kiếm). ORs with the existing DongBang-driven force-
            # crit so a frozen-target cast also keeps its guaranteed crit.
            # AttackStats is frozen, so re-build via ``dataclasses.replace``.
            if skill_data.get("force_crit") and not attack_stats.force_crit:
                from dataclasses import replace as _dc_replace
                attack_stats = _dc_replace(attack_stats, force_crit=True)
            defense_stats = build_defense_stats(target, target_mods, actor, spd_evasion_bonus)
            pen_pct = lc_effects.get_pen_pct(actor, session.rng, session.log)
            result = calculate_damage(skill_obj, attack_stats, defense_stats, session.rng, pen_pct)
        finally:
            if prev_pen is not None:
                actor.element_pen[skill_elem] = prev_pen
            if prev_crit_dmg_rating is not None:
                actor.crit_dmg_rating = prev_crit_dmg_rating
        dmg = result.final
        crit_tag = " 💥BẠO KÍCH!" if result.is_crit else ""

        if result.is_evaded:
            session.log.append(
                f"  🌀 **{actor.name}** dùng *{skill_data['vi']}* → **{target.name}** né tránh!"
            )
            # Per-fight evade counter — drives the actor's
            # ``proc_on_target_evades`` reactive procs (e.g. Bắc Minh Hữu Ngư
            # fires when the target has dodged 3 attacks). Counted on the
            # defender so each attacker reads the same shared count.
            target.evades_count += 1
            # Quỷ Ảnh Mê Tung — successful evade bumps the stack counter on
            # the defender, capped at ``quy_anh_max_stacks`` from the buff's
            # stat_bonus config (default 3). Each stack folds into evasion
            # + spd via ``get_combat_modifiers``'s expansion step.
            if target.has_effect(EffectKey.BUFF_QUY_ANH_ME_TUNG):
                qa_meta = EFFECTS[EffectKey.BUFF_QUY_ANH_ME_TUNG]
                cap = int(qa_meta.stat_bonus.get("quy_anh_max_stacks", 3))
                if target.quy_anh_stacks < cap:
                    target.quy_anh_stacks += 1
                    session.log.append(
                        f"    👤 **{target.name}** Quỷ Ảnh Mê Tung "
                        f"[×{target.quy_anh_stacks}/{cap}]"
                    )
            # Ma Long Xuất Uyên — counter strike on successful evade. Deals
            # a small Âm hit (base + matk_pct × defender.matk) directly to
            # the attacker via ``take_damage`` (so shield + Endure still
            # apply), then rolls for a Cắt Đứt Linh Khí debuff. Hard-CC
            # immunity / debuff_immune_pct still gate the debuff via the
            # standard ``inflict_debuff`` path.
            if target.has_effect(EffectKey.BUFF_MA_LONG_XUAT_UYEN) and actor.is_alive():
                ml_meta = EFFECTS[EffectKey.BUFF_MA_LONG_XUAT_UYEN]
                base = int(ml_meta.stat_bonus.get("ma_long_counter_base_dmg", 0))
                matk_pct = float(ml_meta.stat_bonus.get("ma_long_counter_matk_pct", 0))
                counter_dmg = max(1, base + int(target.matk * matk_pct))
                shield_before = actor.shield
                actor.take_damage(counter_dmg)
                absorbed = shield_before - actor.shield
                tag = colorize_damage(f"-{counter_dmg:,} HP", "am")
                session.log.append(
                    f"    🐉 **{target.name}** Ma Long Xuất Uyên phản kích → {tag}"
                    + (f" 🛡️-{absorbed:,}" if absorbed > 0 else "")
                )
                debuff_chance = float(
                    ml_meta.stat_bonus.get("ma_long_counter_debuff_chance", 0)
                )
                effective = _effective_debuff_chance(debuff_chance, target, actor)
                if effective >= 1.0 or session.rng.random() < effective:
                    cat_dut = EFFECTS[EffectKey.DEBUFF_CAT_DUT]
                    inflict_debuff(
                        session, EffectKey.DEBUFF_CAT_DUT.value, cat_dut,
                        actor, actor=target,
                    )
            # Buff-driven on-evade reactive casts — any active buff that
            # carries a ``proc_on_holder_evade_cast`` config fires the
            # named skill back at the attacker when its holder evades.
            # Resolution order (override wins):
            #   1. Per-instance ``effect_overrides[<buff>]
            #      .proc_on_holder_evade_cast`` (stamped from the granting
            #      skill's JSON — keeps the chain target data-side).
            #   2. ``EffectMeta.proc_on_holder_evade_cast`` (Python default
            #      for buffs that prefer to declare it inline).
            # Equipping gate: the named skill must be in the holder's
            # ``skill_keys``. Includes Lưu Quang Huyễn Ảnh → Cực Quang Trảm
            # and any future buff that opts into this generic hook.
            if actor.is_alive():
                for _active_key in list(target.effects):
                    _meta_b = EFFECTS.get(_active_key)
                    if _meta_b is None:
                        continue
                    _ovr = target.effect_overrides.get(_active_key) or {}
                    _proc_key = (
                        _ovr.get("proc_on_holder_evade_cast")
                        or _meta_b.proc_on_holder_evade_cast
                    )
                    if not _proc_key:
                        continue
                    if _proc_key not in target.skill_keys:
                        continue
                    _proc_data = registry.get_skill(_proc_key)
                    if _proc_data is None:
                        continue
                    session.log.append(
                        f"    {_meta_b.emoji} **{target.name}** {_meta_b.vi} "
                        f"→ phản chiêu **{_proc_data.get('vi', _proc_key)}**"
                    )
                    cast_skill(
                        session, target, actor,
                        _proc_key, _proc_data, 0,
                        _suppress_extras=True,
                    )
            # ``proc_on_self_evade`` reactive — the DEFENDER may carry a
            # skill that fires every time they successfully evade (no
            # threshold). Used by counter-cast patterns like Hỏa Vân Tàn
            # Ảnh Kiếm. Fires BEFORE ``proc_on_target_evades`` so the
            # defender's reaction lands before the attacker's punish hook.
            # Both hooks are gated by ``_suppress_extras`` so the inner
            # reactive cast (which may itself evade) doesn't recurse
            # infinitely.
            if not _suppress_extras:
                session._fire_self_evade_procs(target, actor)
                # ``proc_on_target_evades`` reactive — the ATTACKER may
                # carry a skill that fires when the defender's evade
                # counter crosses N (e.g. Bắc Minh Hữu Ngư, every 3
                # evades). Mirrors the ``proc_on_hits_taken`` pattern but
                # reads target.evades_count.
                session._fire_target_evades_procs(actor, target)
        else:
            # Target damage reduction → actor's HP/MP/evasion/shield/mana-stack scaling
            target_dr = effective_damage_reduction(target, target_mods)
            if target_dr > 0:
                dmg = int(dmg * (1.0 - target_dr))
            # Defender-side vulnerability multiplier — mirror of
            # ``final_dmg_reduce``. Applied AFTER mitigation so a +50% taken
            # bonus combos predictably with reduction (e.g. 50% DR + 50%
            # taken_bonus → ×0.5 ×1.5 = ×0.75 net). Aggregated from active
            # effects (e.g. AuraHuyDiet stamps it on both sides).
            taken_bonus = float(target_mods.get("final_dmg_taken_bonus", 0.0))
            if taken_bonus != 0.0:
                dmg = max(0, int(dmg * (1.0 + taken_bonus)))
            dmg = apply_damage_scaling(dmg, actor, actor_mods)

            # Moc build: consume queued heal→damage built up from prior heals
            if actor.queued_heal_dmg > 0:
                dmg += actor.queued_heal_dmg
                session.log.append(
                    f"    🌿 Dưỡng Sinh Hóa Sát — +{actor.queued_heal_dmg:,} ST từ máu hồi"
                )
                actor.queued_heal_dmg = 0

            # Shield-piercer skills — amp damage when target carries a shield,
            # then split the strike into a shield-routed chunk and a portion
            # that lands directly on HP. Both fields default to 0 so existing
            # skills keep their single-pass behavior.
            shielded_bonus = float(skill_data.get("bonus_dmg_vs_shielded", 0.0))
            if shielded_bonus > 0 and target.shield > 0:
                dmg = max(1, int(dmg * (1.0 + shielded_bonus)))

            # HP-threshold amp — Phá Phủ Trầm Chu style "hits harder while the
            # target is fresh". Spec carries an HP fraction and a multiplier:
            #   "bonus_dmg_vs_hp_above": {"hp_pct": 0.70, "dmg_bonus": 0.40}
            # → +40% dmg while target is over 70% HP. Symmetric `_hp_below`
            # form supported for future executioner-class skills. Both gates
            # are evaluated AFTER mitigation so the displayed dmg already
            # reflects the boost.
            hp_above_spec = skill_data.get("bonus_dmg_vs_hp_above")
            if hp_above_spec and target.hp_max > 0:
                threshold = float(hp_above_spec.get("hp_pct", 1.0))
                amp = float(hp_above_spec.get("dmg_bonus", 0.0))
                if amp > 0 and (target.hp / target.hp_max) > threshold:
                    dmg = max(1, int(dmg * (1.0 + amp)))
                    session.log.append(
                        f"    🪓 *Phá Phủ* — +{int(amp * 100)}% ST "
                        f"(HP địch > {int(threshold * 100)}%)"
                    )
            hp_below_spec = skill_data.get("bonus_dmg_vs_hp_below")
            if hp_below_spec and target.hp_max > 0:
                threshold = float(hp_below_spec.get("hp_pct", 0.0))
                amp = float(hp_below_spec.get("dmg_bonus", 0.0))
                if amp > 0 and (target.hp / target.hp_max) < threshold:
                    dmg = max(1, int(dmg * (1.0 + amp)))

            # Fire-DoT-count amp — Liệt Diễm Phần Thiên Chưởng-style.
            # Multiplies damage by ``per_dot_pct × <distinct fire DoT kinds
            # on target>``. Counted via ``count_elemental_dots`` so the gate
            # matches the DoT pipeline (markers like Hỏa Xuyên Thấu / Hồng
            # Liên don't count — only fire effects that actually tick).
            fire_dot_amp = float(skill_data.get("bonus_dmg_per_fire_dot_pct", 0.0))
            if fire_dot_amp > 0:
                from src.game.engine.effects import count_elemental_dots
                fire_dot_count = count_elemental_dots(target, "hoa")
                if fire_dot_count > 0:
                    amp = fire_dot_amp * fire_dot_count
                    dmg = max(1, int(dmg * (1.0 + amp)))
                    session.log.append(
                        f"    🔥 *Liệt Diễm* — +{int(amp * 100)}% ST "
                        f"({fire_dot_count} loại Hỏa DoT)"
                    )

            # Per-target-stack final-damage multiplier — companion to the
            # pre-cast crit_dmg/pen bumps already applied above. Multiplies
            # damage by ``1 + dmg_pct × stack_count``.
            if target_stack_scale and target_stack_count > 0:
                _dmg_pct = float(target_stack_scale.get("dmg_pct", 0.0))
                if _dmg_pct > 0:
                    _amp = _dmg_pct * target_stack_count
                    dmg = max(1, int(dmg * (1.0 + _amp)))
                    session.log.append(
                        f"    🗡️ *Sậu Thiên* — +{int(_amp * 100)}% ST "
                        f"(×{target_stack_count} {target_stack_scale.get('stack')} stack)"
                    )

            # Passive: per-element-DoT damage scaling (Dung Linh Chân Quyết-
            # style). Scans the actor's owned passives — any with
            # ``passive_dmg_per_elem_dot_pct`` whose element matches the
            # current skill's element contributes ``pct × count`` to the
            # damage multiplier. Stacks additively across multiple passives.
            if skill_elem:
                from src.game.engine.effects import count_elemental_dots
                passive_dmg_amp = 0.0
                passive_dot_count = 0
                passive_label = None
                for _sk in actor.skill_keys:
                    _pdata = registry.get_skill(_sk)
                    if not _pdata:
                        continue
                    _spec = _pdata.get("passive_dmg_per_elem_dot_pct")
                    if not _spec or _spec.get("element") != skill_elem:
                        continue
                    _pct = float(_spec.get("pct", 0.0))
                    if _pct <= 0:
                        continue
                    _count = count_elemental_dots(target, skill_elem)
                    if _count <= 0:
                        continue
                    passive_dmg_amp += _pct * _count
                    passive_dot_count = _count
                    passive_label = _pdata.get("vi", _sk)
                if passive_dmg_amp > 0:
                    dmg = max(1, int(dmg * (1.0 + passive_dmg_amp)))
                    session.log.append(
                        f"    🔥 *{passive_label}* — +{int(passive_dmg_amp * 100)}% ST "
                        f"({passive_dot_count} loại {skill_elem.upper()} DoT)"
                    )
            # Thủy Vi — caster-side per-cast shield bypass charges. When
            # active, folds the buff's stored ``thuy_vi_bypass_pct`` into
            # this cast's shield_bypass on top of any per-skill bypass the
            # skill itself declared. The buff was applied AFTER the damage
            # step on its grant cast, so Thủy Vi never burns its own charge.
            thuy_vi_bonus = 0.0
            thuy_vi_left = 0
            if actor.has_effect(EffectKey.BUFF_THUY_VI):
                tv_ov = (actor.effect_overrides.get(EffectKey.BUFF_THUY_VI) or {}).get("stat_bonus") or {}
                thuy_vi_left = int(tv_ov.get("thuy_vi_charges", 0))
                if thuy_vi_left > 0:
                    thuy_vi_bonus = float(tv_ov.get("thuy_vi_bypass_pct", 0.0))
            bypass_pct = max(0.0, min(
                1.0,
                float(skill_data.get("shield_bypass_pct", 0.0)) + thuy_vi_bonus,
            ))
            bypass_dmg = int(dmg * bypass_pct) if bypass_pct > 0 else 0
            shielded_dmg = dmg - bypass_dmg

            # BuffBatTu — prevent killing blow once. Computed against post-shield
            # HP loss because the shield absorbs first inside ``take_damage``;
            # only damage that would actually leak to HP can trigger Bất Tử.
            # The bypass portion lands directly on HP, so it counts in full
            # toward the killing-blow check.
            hp_dmg_preview = max(0, shielded_dmg - target.shield) + bypass_dmg
            if hp_dmg_preview >= target.hp and target.has_effect(EffectKey.BUFF_BAT_TU):
                # Recalculate the split so total damage leaves HP at 1 after
                # the shield drains and the bypass chunk lands.
                survivable = target.hp - 1 + target.shield
                dmg = max(0, survivable)
                bypass_dmg = int(dmg * bypass_pct) if bypass_pct > 0 else 0
                shielded_dmg = dmg - bypass_dmg
                target.effects.pop(EffectKey.BUFF_BAT_TU, None)
                session.log.append(f"    💫 **{target.name}** kích hoạt **Bất Tử** — sống sót!")

            shield_before = target.shield
            if shielded_dmg > 0:
                target.take_damage(shielded_dmg)
            if bypass_dmg > 0:
                target.take_damage(bypass_dmg, bypass_shield=True)
            dealt_total += dmg
            absorbed = shield_before - target.shield
            if absorbed > 0:
                session.log.append(f"    🛡️ Hộ Thuẫn hấp thụ {absorbed:,} sát thương!")
            if bypass_dmg > 0:
                session.log.append(
                    f"    🌟 Xuyên Thuẫn — {bypass_dmg:,} sát thương đi thẳng HP!"
                )

            # Capture context flags for chain-skill cast_on triggers. ``OR``
            # rather than assignment so a multi-strike skill that reaches
            # this block N times sets each flag once it occurs (e.g. shield
            # broken on hit 2 stays True for the chain dispatch at the end).
            cast_ctx["is_crit"] = cast_ctx["is_crit"] or bool(result.is_crit)
            cast_ctx["killed"] = cast_ctx["killed"] or (target.hp <= 0)
            cast_ctx["shield_broken"] = (
                cast_ctx["shield_broken"]
                or (shield_before > 0 and target.shield == 0)
            )

            # Thủy Vi — consume one charge whenever the buff was the source of
            # any bypass on this cast. Drops the buff entirely when the
            # counter hits 0; otherwise writes the decremented charge count
            # back onto the override so the next cast sees N-1.
            if thuy_vi_left > 0 and thuy_vi_bonus > 0:
                new_left = thuy_vi_left - 1
                if new_left <= 0:
                    actor.effects.pop(EffectKey.BUFF_THUY_VI, None)
                    actor.effect_overrides.pop(EffectKey.BUFF_THUY_VI, None)
                    session.log.append(
                        f"    💧 **{actor.name}** Thủy Vi tan biến (cạn linh khí)."
                    )
                else:
                    ov = actor.effect_overrides.setdefault(EffectKey.BUFF_THUY_VI, {})
                    ov.setdefault("stat_bonus", {})["thuy_vi_charges"] = new_left
                    session.log.append(
                        f"    💧 **{actor.name}** Thủy Vi xuyên "
                        f"{int(thuy_vi_bonus * 100)}% Hộ Thuẫn (còn {new_left} đòn)"
                    )

            skill_elem = skill_data.get("element")
            dmg_tag = colorize_damage(f"-{dmg:,} HP", skill_elem)
            session.log.append(
                f"  ⚡ **{actor.name}** dùng *{skill_data['vi']}* → {dmg_tag}{crit_tag}"
                f" | {target.name}: {target.hp:,}/{target.hp_max:,} HP"
            )

            # Lưu Quang Huyễn Ảnh — each incoming skill hit ramps the buff's
            # spd_pct override by +0.10, capped at +0.30. One bump per cast
            # (multi-strike / formation echoes don't compound). Duration is
            # preserved — no refresh on the bump.
            _bump_luu_quang_stack(session, target, dmg)

            # Hải Thị Thận Lâu active — every damaging cast rolls the buff's
            # ``hai_thi_slow_chance`` (default 60%); on success, stamps
            # DebuffLamCham on the target with a ``spd_pct`` override read
            # from ``hai_thi_slow_magnitude`` (default -20%). Routes through
            # ``inflict_debuff`` so target's debuff_immune_pct + actor's
            # debuff_apply_bonus combine via the standard chance formula,
            # and hard-CC immunity gates are respected.
            slow_chance = float(actor_mods.get("hai_thi_slow_chance", 0.0))
            if slow_chance > 0 and target.is_alive() and dmg > 0:
                eff = _effective_debuff_chance(slow_chance, actor, target)
                if eff >= 1.0 or session.rng.random() < eff:
                    slow_mag = float(actor_mods.get("hai_thi_slow_magnitude", -0.20))
                    inflict_debuff(
                        session, EffectKey.DEBUFF_LAM_CHAM,
                        EFFECTS[EffectKey.DEBUFF_LAM_CHAM], target,
                        actor=actor,
                        overrides={"stat_bonus": {"spd_pct": slow_mag}},
                    )

            # Multi-Strike — per-attack chance the actor lands a follow-up hit
            # for ``multi_strike_dmg_pct`` of the original damage. Reuses the
            # same shield/HP path so DR + element_res still apply. Skipped if
            # the target is already dead; skipped on true-damage ticks below.
            if (
                target.is_alive()
                and actor.multi_strike_pct > 0
                and session.rng.random() < actor.multi_strike_pct
            ):
                follow = max(1, int(dmg * actor.multi_strike_dmg_pct))
                shield_before2 = target.shield
                target.take_damage(follow)
                absorbed2 = shield_before2 - target.shield
                follow_tag = colorize_damage(f"-{follow:,} HP", skill_elem)
                session.log.append(
                    f"    ✨ **{actor.name}** Liên Kích → {follow_tag}"
                    + (f" 🛡️-{absorbed2:,}" if absorbed2 > 0 else "")
                    + f" | {target.name}: {target.hp:,}/{target.hp_max:,} HP"
                )

            apply_true_damage(
                actor, target,
                base_damage=dmg, is_crit=result.is_crit,
                log=session.log,
                skill_pct=float(skill_data.get("true_dmg_pct", 0.0)),
            )

            run_on_hit_procs(session, actor, target, is_crit=result.is_crit)

            # Cửu Khúc Hoàng Hà — formation auto-stamp. Every successful
            # damaging skill hit applies N marks (N = ``cuu_khuc_per_hit``,
            # set by the formation bonuses). Routed through inflict_debuff
            # so the stack-add + snapshot path runs uniformly. Skipped on
            # suppressed sub-casts so multi-hit / chain follow-ups don't
            # double-stamp a single logical skill use.
            if (
                not _suppress_extras
                and int(actor.cuu_khuc.get("per_hit", 0)) > 0
                and target.is_alive()
                and dmg > 0
            ):
                cuu_khuc_meta = EFFECTS.get(EffectKey.DEBUFF_CUU_KHUC)
                if cuu_khuc_meta is not None:
                    inflict_debuff(
                        session, EffectKey.DEBUFF_CUU_KHUC, cuu_khuc_meta,
                        target, actor=actor,
                    )

            # 9-mark followup — at full Cửu Khúc stack, the formation also
            # fires its declared follow-up skill back at the target. The
            # skill key flows from formation bonuses → CombatStats →
            # ``actor.cuu_khuc_followup_skill_key`` (default
            # ``SkillAtkThuyLongDan_R7``), so different formations can
            # declare different chain targets. Power scales with
            # ``cuu_khuc_followup_pct`` (0.40 base, 1.0 at gem 10).
            # ``_suppress_extras=True`` so the followup's own hit can't
            # recursively trigger another mark / followup pass.
            # Equipping gate: the chain skill must be in actor.skill_keys.
            _ck_followup_key = str(actor.cuu_khuc.get("followup_skill_key", ""))
            _ck_followup_pct = float(actor.cuu_khuc.get("followup_pct", 0.0))
            if (
                not _suppress_extras
                and _ck_followup_pct > 0
                and target.is_alive()
                and target.cuu_khuc_stacks >= effective_stack_cap(target, EffectKey.DEBUFF_CUU_KHUC.value)
                and _ck_followup_key
                and _ck_followup_key in actor.skill_keys
            ):
                tld_data = registry.get_skill(_ck_followup_key)
                if tld_data is not None:
                    pct = _ck_followup_pct
                    scaled = dict(tld_data)
                    scaled["base_dmg"] = max(1, int(tld_data.get("base_dmg", 0) * pct))
                    # Strip the parent's own chain so a freshly Thủy Long-Ngâm
                    # cycle doesn't piggy-back off the Cửu Khúc burst.
                    scaled.pop("chain_skill", None)
                    session.log.append(
                        f"  🐉 **{actor.name}** · **Cửu Khúc Hoàng Hà** "
                        f"(9 ấn) → **{tld_data.get('vi', _ck_followup_key)}** "
                        f"({pct * 100:.0f}% ST):"
                    )
                    cast_skill(
                        session, actor, target, _ck_followup_key,
                        scaled, mp_cost=0, _suppress_extras=True,
                    )

            apply_life_steal(session, actor, dmg)
            session._apply_mana_gains(actor, dmg)
            apply_reactive_damage(session, actor, target, dmg)

            # On-hit: Linh Căn procs (consolidated in effects.py)
            lc_effects.on_hit(actor, target, dmg, result.is_crit, session.rng, session.log)

            # Ma Khí Hộ Thể — every Âm hit converts a fraction of the damage
            # dealt into shield for the actor. ``am_hit_to_shield_pct`` is
            # read from ``get_combat_modifiers`` so any future buff / passive
            # contributing to the same stat stacks naturally.
            if skill_elem == "am" and dmg > 0:
                shield_pct = float(actor_mods.get("am_hit_to_shield_pct", 0.0))
                if shield_pct > 0:
                    raw = max(1, int(dmg * shield_pct))
                    gained = actor.add_shield(raw)
                    if gained > 0:
                        session.log.append(
                            f"    🩻 **{actor.name}** Ma Khí Hộ Thể "
                            f"+{gained:,} khiên "
                            f"({actor.shield:,}/{actor.shield_cap():,})"
                        )

            # Consume Đông Băng — a frozen target's first non-evaded hit
            # auto-crits (force_crit set in build_attack_stats), then the
            # freeze breaks. After all on-hit procs so any "vs frozen"
            # readers still see the debuff during this hit.
            if attack_stats.force_crit and target.has_effect(EffectKey.DEBUFF_DONG_BANG):
                target.effects.pop(EffectKey.DEBUFF_DONG_BANG, None)
                target.effect_overrides.pop(EffectKey.DEBUFF_DONG_BANG, None)
                session.log.append(f"    🧊 Đông Băng vỡ tan trong đòn bạo kích!")

        # Apply skill debuff/CC effects to target
        apply_skill_effects(session, skill_data, actor, target, hit=not result.is_evaded)

    elif skill_data.get("debuff_only"):
        # 0-damage debuff aura — bypasses the damage roll but still stamps
        # the skill's ``effects`` list on the target. Used by passive
        # formation auras (e.g. Phong Đô Ma Trận) that only exist to
        # debuff what they encircle.
        session.log.append(f"  🪬 **{actor.name}** dùng *{skill_data['vi']}*")
        apply_skill_effects(session, skill_data, actor, target, hit=True)
    else:
        # Support / defense skill — applies effects to self (actor)
        session.log.append(f"  🛡️ **{actor.name}** dùng *{skill_data['vi']}*")
        apply_support_skill(session, skill_data, actor, target)

    if _suppress_extras:
        # Chained / multi-hit follow-ups skip cooldown + per-cast hooks so
        # they don't double-trigger charge / chain / summon / cooldown.
        return

    actor.set_cooldown(skill_key, skill_data.get("cooldown", 1))
    # Per-fight ``usage_limit`` counter — only top-level casts bump the
    # counter (multi-hit follow-ups already short-circuit above). Filtering
    # against the limit lives in ``CombatSession._choose_skill``.
    actor.skill_usage_count[skill_key] = actor.skill_usage_count.get(skill_key, 0) + 1

    # ── Skill-extras hooks (opt-in via JSON fields) ──────────────────────
    # 1. Multi-hit: replay damage path ``hit_count - 1`` more times. Each
    #    follow-up rolls its own crit/evade and fires on-hit procs but pays
    #    no MP and won't double-tick charge / chain / summon hooks.
    hit_count = max(1, int(skill_data.get("hit_count", 1)))
    for _ in range(hit_count - 1):
        if not target.is_alive():
            break
        session.log.append(f"  🔁 **{actor.name}** liên kích bồi thêm:")
        cast_skill(
            session, actor, target, skill_key, skill_data,
            mp_cost=0, _suppress_extras=True,
        )

    # 2. Charge bonus — increment cast count, detonate on every Nth cast.
    if base_dmg > 0:
        apply_charge_bonus(session, actor, target, skill_key, skill_data, dealt_total)

    # 3. Chain skill — automatically fire a follow-up skill at scaled damage.
    #    ``skill_key`` is forwarded so ``cast_on: every_n_casts`` has a
    #    stable per-parent-skill counter on ``actor.skill_chain_counts``.
    #    ``cast_ctx`` carries the trigger flags (is_crit / killed /
    #    shield_broken) collected during this cast's damage step.
    cast_chain_skill(session, actor, target, skill_key, skill_data, cast_ctx)

    # 5. Summon spawn — append a new summon that ticks each round.
    maybe_spawn_summon(session, actor, skill_data)

    # 6. Consume target debuffs — finisher-class skills (Cửu U Thần Trảo)
    #    strip N debuffs from the target after the hit lands. Counts only
    #    ``EffectKind.DEBUFF`` entries so CC + buffs are untouched. Removed
    #    AFTER damage + apply_skill_effects so the consumed debuffs already
    #    contributed their amp to this cast.
    consume_n = int(skill_data.get("consume_target_debuffs", 0))
    if consume_n > 0 and target.is_alive():
        debuff_keys = [
            k for k in list(target.effects)
            if (m := EFFECTS.get(k)) and m.kind is EffectKind.DEBUFF
        ]
        consumed: list[str] = []
        for k in debuff_keys[:consume_n]:
            target.effects.pop(k, None)
            target.effect_overrides.pop(k, None)
            meta = EFFECTS.get(k)
            consumed.append(meta.vi if meta else k)
        if consumed:
            session.log.append(
                f"    🩸 **{actor.name}** Cửu U nuốt chửng {len(consumed)} trạng thái: "
                f"{', '.join(consumed)}"
            )

    # 7. Strip target buffs — Phán-Quyết-class skills randomly tear N buffs
    #    off the target after the hit. Counts only ``EffectKind.BUFF`` so
    #    debuffs on the target are untouched. Random selection (via session
    #    rng) so the player can't game application order to shield the most
    #    valuable buff.
    strip_n = int(skill_data.get("strip_target_buffs", 0))
    if strip_n > 0 and target.is_alive():
        buff_keys = [
            k for k in list(target.effects)
            if (m := EFFECTS.get(k)) and m.kind is EffectKind.BUFF
        ]
        if buff_keys:
            picks = session.rng.sample(buff_keys, min(strip_n, len(buff_keys)))
            stripped: list[str] = []
            for k in picks:
                target.effects.pop(k, None)
                target.effect_overrides.pop(k, None)
                meta = EFFECTS.get(k)
                stripped.append(meta.vi if meta else k)
            if stripped:
                session.log.append(
                    f"    ☀️ **{actor.name}** Thánh Quang tước đoạt {len(stripped)} buff: "
                    f"{', '.join(stripped)}"
                )


def fire_formation_skills(
    session: "CombatSession", actor: Combatant, target: Combatant,
    main_skill_data: dict | None = None,
) -> None:
    """Parallel formation barrage — after the main cast, each active
    formation's signature skill auto-fires if it's off cooldown and the
    actor can afford the MP. Unlike the main rotation, a formation skill
    that can't afford MP simply sits out (no auto-attack fallback).
    Insufficient-cooldown and MP guards are silent to keep the log clean.

    A formation skill flagged ``per_hit_followup`` is a pure passive
    proc: it never normal-casts, so it bypasses the cooldown / MP gates
    and pays no per-cast MP. It echoes once per hit of the just-cast main
    skill (a 7-hit attack fires the formation 7 times). Each echo runs
    with ``_suppress_extras=True`` so it can't recursively trigger
    chain / multi-hit / summon / cooldown hooks. The formation's only
    cost to the player is the standing ``reserved_mp_pct`` while active.
    """
    if not actor.formation_skill_keys:
        return
    main_hits = max(1, int((main_skill_data or {}).get("hit_count", 1)))
    for frm_key in actor.formation_skill_keys:
        if not target.is_alive():
            break
        skill_data = registry.get_skill(frm_key)
        if not skill_data:
            continue

        if skill_data.get("per_hit_followup"):
            # Threshold-fed bonus echoes (e.g. Thập Nhị Đô's 10-gem tier
            # adds +1) stack onto the per-hit count from the main skill.
            echoes = main_hits + max(0, int(getattr(actor, "formation_echo_bonus", 0)))
            session.log.append(
                f"  🔯 **{actor.name}** · **{skill_data.get('vi', frm_key)}** (đòn bồi sát ×{echoes}):"
            )
            for _ in range(echoes):
                if not target.is_alive():
                    break
                cast_skill(
                    session, actor, target, frm_key, skill_data,
                    mp_cost=0, _suppress_extras=True,
                )
            continue

        # Standard formation skill — gated by cooldown + MP. Run the per-element
        # MP cost multiplier so the song-phát affordability check matches the spend.
        if actor.skill_on_cooldown(frm_key):
            continue
        from src.game.systems.combat.helpers import effective_mp_cost
        mp_cost = effective_mp_cost(actor, skill_data) if "mp_cost" in skill_data else 999
        if actor.mp < mp_cost:
            continue
        session.log.append(
            f"  🔯 **{actor.name}** · **{skill_data.get('vi', frm_key)}** (trận song phát):"
        )
        cast_skill(session, actor, target, frm_key, skill_data, mp_cost)


def apply_support_skill(
    session: "CombatSession", skill_data: dict, actor: Combatant, target: Combatant
) -> None:
    """Handle a support/defense skill: instant heals and buff application.

    Instant heal/MP magnitudes come from ``meta.instant_heal_pct`` /
    ``meta.instant_mp_pct`` (with per-cast override via
    ``effect_overrides[<key>]``). Apply chance falls back to
    ``meta.apply_chance`` when the skill JSON omits ``effect_chances[<key>]``.
    """
    effect_chances: dict[str, float] = skill_data.get("effect_chances", {})
    effect_overrides: dict[str, dict] = skill_data.get("effect_overrides", {})
    for effect_key in skill_data.get("effects", []):
        # ── Special-keyword effects (no EFFECTS entry, run on the target) ──
        # Mirrors apply_skill_effects so a no-damage skill (base_dmg=0,
        # routed here) can still trigger soul-drain / stat-steal pulses.
        if effect_key == "ApplySoulDrain":
            for _ in range(max(1, int(skill_data.get("soul_drain_procs", 1)))):
                apply_soul_drain(session, actor, target)
            continue
        if effect_key == "ApplyStatSteal":
            per_proc_pct = skill_data.get("stat_steal_pct")
            for _ in range(max(1, int(skill_data.get("stat_steal_procs", 1)))):
                apply_stat_steal(
                    session, actor, target,
                    per_proc_pct=float(per_proc_pct) if per_proc_pct else None,
                )
            continue
        if effect_key == "ApplyBuffSteal":
            for _ in range(max(1, int(skill_data.get("buff_steal_count", 1)))):
                apply_buff_steal(session, actor, target)
            continue
        meta = EFFECTS.get(effect_key)
        if meta is None:
            continue
        override = effect_overrides.get(effect_key) or {}

        # ── Instant HP pulse (HpRegen + future cleanse-with-heal effects) ──
        heal_pct = float(override.get("instant_heal_pct", meta.instant_heal_pct))
        if heal_pct > 0:
            actor_mods = get_combat_modifiers(actor)
            heal_mult = 1.0 + actor.heal_pct + actor_mods.get("hp_regen_pct", 0.0)
            requested = max(1, int(actor.hp_max * heal_pct * heal_mult))
            if actor.bleed_stacks > 0 and actor.bleed_heal_reduce > 0:
                session.log.append(
                    f"    🩸 *Chảy Máu giảm hiệu lực hồi máu "
                    f"{actor.bleed_heal_reduce * 100:.0f}%*"
                )
            applied = session._apply_heal(actor, requested)
            session.log.append(f"    ❤️ +{applied:,} HP")

        # ── Instant MP pulse (MpRegen + future mana-burst effects) ────────
        mp_pct = float(override.get("instant_mp_pct", meta.instant_mp_pct))
        if mp_pct > 0:
            regen = max(1, int(actor.mp_max * mp_pct))
            actor.mp = min(actor.mp_max, actor.mp + regen)
            session.log.append(f"    💙 +{regen:,} MP")

        # If this is a pure-pulse effect (no kind-specific behavior beyond
        # the instant heal/mp above), we're done with this slot. Skips the
        # buff/debuff branches so an HpRegen pulse doesn't also try to
        # ``apply_effect`` a stat-less buff onto the actor.
        if (heal_pct > 0 or mp_pct > 0) and not meta.stat_bonus and not meta.dot_pct:
            continue

        if meta.kind.value == "buff":
            # Apply buff to self (actor) — overrides may carry custom duration
            # or stronger stat_bonus values.
            dur = int(override.get("duration", default_duration(effect_key)))
            stamp = {k: v for k, v in override.items() if k != "duration"} or None
            actor.apply_effect(effect_key, dur, overrides=stamp)
            session.log.append(
                f"    {meta.emoji} **{meta.vi}** ({dur}t) — {meta.description_vi}"
            )

        elif meta.kind.value in ("debuff", "cc"):
            # CC skills with base_dmg=0 that debuff the target (e.g. CCBind skill)
            base_chance = effect_chances.get(effect_key, meta.apply_chance)
            effective_chance = _effective_debuff_chance(base_chance, actor, target)
            if effective_chance >= 1.0 or session.rng.random() < effective_chance:
                inflict_debuff(
                    session, effect_key, meta, target, actor=actor,
                    overrides=override or None,
                )


def apply_skill_effects(
    session: "CombatSession", skill_data: dict,
    actor: Combatant, target: Combatant, hit: bool,
) -> None:
    """Apply all effect_keys from a skill's effects list to the appropriate target.

    For debuffs/CC: checks the skill's ``effect_chances`` dict, falling back
    to ``meta.apply_chance`` when the skill doesn't specify a per-effect
    chance. The result is multiplied by ``(1 - target.debuff_immune_pct)``
    to get the effective proc probability.

    Special keywords handled in-line (not in the EFFECTS registry).
    """
    effect_chances: dict[str, float] = skill_data.get("effect_chances", {})
    effect_overrides: dict[str, dict] = skill_data.get("effect_overrides", {})
    for effect_key in skill_data.get("effects", []):
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
                # Per-skill ``stat_steal_pct`` (when present) overrides the
                # global per-proc magnitude. Lets a "single big steal" skill
                # like Sưu Hồn Đoạt Phách take 25% in one shot rather than
                # chaining 6 small 4% procs.
                per_proc_pct = skill_data.get("stat_steal_pct")
                for _ in range(max(1, int(skill_data.get("stat_steal_procs", 1)))):
                    apply_stat_steal(
                        session, actor, target,
                        per_proc_pct=float(per_proc_pct) if per_proc_pct else None,
                    )
            continue
        # ── Special: buff-steal — rip one stealable buff per proc ──────────
        if effect_key == "ApplyBuffSteal":
            if hit:
                for _ in range(max(1, int(skill_data.get("buff_steal_count", 1)))):
                    apply_buff_steal(session, actor, target)
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
            # Phượng Hoàng Chân Hỏa — the skill's ``effect_overrides.DebuffPhuongHoa``
            # tunes the debuff that this buff reflects onto attackers. Stash it
            # inside the buff's own override entry under ``_phuong_hoa_emit``
            # so it travels with the buff and auto-cleans when the buff
            # expires (tick_effects pops the override dict). The reflective
            # hook in ``procs.apply_reactive_damage`` reads it back.
            if effect_key == EffectKey.BUFF_PHUONG_HOANG_CHAN_HOA.value:
                emit_ovr = effect_overrides.get("DebuffPhuongHoa")
                if emit_ovr:
                    stamp = dict(stamp or {})
                    stamp["_phuong_hoa_emit"] = emit_ovr
                    stamp = stamp or None
            actor.apply_effect(effect_key, dur, overrides=stamp)
            session.log.append(f"    {meta.emoji} **{actor.name}** nhận **{meta.vi}** ({dur}t)")
        elif hit and meta.kind.value in ("debuff", "cc"):
            base_chance = effect_chances.get(effect_key, meta.apply_chance)
            effective_chance = _effective_debuff_chance(base_chance, actor, target)
            if effective_chance >= 1.0 or session.rng.random() < effective_chance:
                effective_override = override
                # Xích Luyện Tỏa Hồn — layer the actor's per-gem reductions onto
                # the debuff's base stat_bonus so the same DebuffXichLuyenToaHon
                # carries weaker/stronger numbers depending on the caster's
                # gem tier. The actor's ``toa_hon_*_pct`` fields are NEGATIVE
                # already (see character_stats); add them to the base -0.10 to
                # form the effective stat_bonus snapshot stamped onto the holder.
                if effect_key == EffectKey.DEBUFF_XICH_LUYEN_TOA_HON.value:
                    amp = getattr(actor, "toa_hon_amp", None) or {}
                    fire_taken = float((getattr(actor, "dmg_taken", None) or {}).get("hoa", 0.0))
                    if amp or fire_taken:
                        base_sb = (override or {}).get("stat_bonus", meta.stat_bonus)
                        merged_sb = dict(base_sb)
                        for stat, delta in amp.items():
                            merged_sb[stat] = max(
                                -1.0,
                                float(base_sb.get(stat, 0.0)) + float(delta),
                            )
                        if fire_taken:
                            merged_sb["fire_dmg_taken"] = (
                                float(base_sb.get("fire_dmg_taken", 0.0)) + fire_taken
                            )
                        effective_override = {**(override or {}), "stat_bonus": merged_sb}
                inflict_debuff(
                    session, effect_key, meta, target, actor=actor,
                    overrides=effective_override,
                )


_NGHIEP_HOA_BUFF_BURN_CHANCE: float = 0.30
_NGHIEP_HOA_DEFAULT_DUR: int = 4


def _fire_nghiep_hoa_hong_lien_reaction(
    session: "CombatSession",
    just_applied_key: str,
    target: Combatant,
    actor: Combatant | None,
) -> None:
    """Per-debuff reaction for holders of ``DebuffNghiepHoaHongLien``.

    Each successful debuff/CC application on the marked target:
      1. Pushes one ``DebuffNghiepHoa`` stack (3 % hp_max fire DoT/turn).
      2. Rolls 30 % to remove one random buff from the target.

    Recursion is gated by skipping the reaction when the just-applied
    effect IS one of the Nghiệp Hỏa pair — otherwise applying the DoT
    would re-enter inflict_debuff and stack infinitely on a single
    debuff hit. The marker itself is also skipped so its own application
    doesn't immediately seed a stack on cast (the spec says marks come
    from *subsequent* debuffs).
    """
    if not target.has_effect(EffectKey.DEBUFF_NGHIEP_HOA_HONG_LIEN):
        return
    if just_applied_key in (
        EffectKey.DEBUFF_NGHIEP_HOA,
        EffectKey.DEBUFF_NGHIEP_HOA_HONG_LIEN,
    ):
        return
    # Add the Nghiệp Hỏa stack via inflict_debuff so build flags propagate
    # and the standard log line fires. Recursion guard above makes this safe.
    nh_meta = EFFECTS.get(EffectKey.DEBUFF_NGHIEP_HOA)
    if nh_meta is not None:
        inflict_debuff(
            session, EffectKey.DEBUFF_NGHIEP_HOA, nh_meta, target, actor=actor,
            overrides={"duration": _NGHIEP_HOA_DEFAULT_DUR},
        )
    # 30 % chance to burn a random buff. Filter on EffectKind.BUFF only —
    # cleanseability is irrelevant for "burn" semantics (it's a forced
    # reaction, not a player-cleanse). Pop both effects + overrides.
    if session.rng.random() < _NGHIEP_HOA_BUFF_BURN_CHANCE:
        buff_keys = [
            k for k in list(target.effects)
            if (m := EFFECTS.get(k)) is not None and m.kind is EffectKind.BUFF
        ]
        if buff_keys:
            burned = session.rng.choice(buff_keys)
            target.effects.pop(burned, None)
            target.effect_overrides.pop(burned, None)
            burned_meta = EFFECTS.get(burned)
            label = burned_meta.vi if burned_meta else burned
            emoji = burned_meta.emoji if burned_meta else "✨"
            session.log.append(
                f"    🔴 **Hồng Liên** thiêu rụi {emoji}{label} trên **{target.name}**"
            )


def _fire_proc_on_target_status(
    session: "CombatSession",
    actor: Combatant | None,
    target: Combatant,
    effect_key: str,
) -> None:
    """Fire any of ``actor``'s skills that proc when ``effect_key`` is applied.

    Mirror of ``_fire_hits_taken_procs`` but keyed on inflicting a status onto
    a target rather than receiving hits. Used for reactive boss patterns like
    Chung Yên's Diệt Tận, which auto-casts the moment Choáng (CCStun) lands.

    Constraints:
      • ``actor`` must be alive (None / dead actor → no-op).
      • Proc skills must declare ``proc_on_target_status`` matching ``effect_key``.
      • MP cost and per-skill cooldown still apply — a misconfigured proc with
        non-zero MP / live cooldown will fizzle silently.
      • Iterates in skill-key order for deterministic logs.

    Bounded recursion: the proc's outgoing hit goes through ``cast_skill``,
    which may apply its own debuffs and re-enter ``inflict_debuff``. We do not
    guard against re-entry here — proc skills must therefore not declare an
    effect matching their own trigger (Diệt Tận has no CCStun on its effects).
    """
    if actor is None or not actor.is_alive():
        return
    for sk in actor.skill_keys:
        data = registry.get_skill(sk)
        if data is None:
            continue
        trigger = data.get("proc_on_target_status")
        if trigger != effect_key:
            continue
        from src.game.systems.combat.helpers import effective_mp_cost
        mp_cost = effective_mp_cost(actor, data)
        if actor.mp < mp_cost:
            continue
        if actor.skill_on_cooldown(sk):
            continue
        meta = EFFECTS.get(effect_key)
        status_name = meta.vi if meta else effect_key
        session.log.append(
            f"  ⚡ **{actor.name}** kích hoạt **{data.get('vi', sk)}** "
            f"(địch dính **{status_name}**)"
        )
        cast_skill(session, actor, target, sk, data, mp_cost)


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
    # Generic per-effect resist — any active buff/passive can carry a
    # ``stat_bonus`` entry keyed ``effect_resist:<effect_key>`` with a 0–1
    # chance to shrug off that specific application. Lets defensive auras
    # like Cửu Dương Hộ Thể declare "90% freeze resist" without bloating
    # Combatant with a dedicated field per resisted effect. Aggregates via
    # ``get_combat_modifiers`` so multiple sources stack additively (capped
    # below at 1.0 so it can never be guaranteed).
    resist_key = f"effect_resist:{effect_key}"
    resist_chance = min(1.0, max(
        0.0, float(get_combat_modifiers(target).get(resist_key, 0.0))
    ))
    if resist_chance > 0 and session.rng.random() < resist_chance:
        session.log.append(
            f"    🛡️ **{target.name}** kháng **{meta.vi}** "
            f"({int(resist_chance * 100)}% kháng)"
        )
        return
    # Tuyệt Diệu Vô Ảnh — total slow immunity. Read the effective stat_bonus
    # (per-instance overrides win over meta) and skip if it carries any
    # negative ``spd_pct``. Catches DebuffLamCham, DebuffTroBuoc, DebuffLunDat,
    # EffectNgungDong, plus any future custom slow stamped via overrides.
    if target.has_effect(EffectKey.BUFF_TUYET_DIEU_VO_ANH):
        ovr_sb = (overrides or {}).get("stat_bonus") or {}
        spd_delta = float(ovr_sb.get("spd_pct", meta.stat_bonus.get("spd_pct", 0.0)))
        if spd_delta < 0:
            session.log.append(
                f"    👣 **{target.name}** trong Tuyệt Diệu Vô Ảnh — "
                f"**{meta.vi}** tan biến!"
            )
            return
    dur = int((overrides or {}).get("duration", default_duration(effect_key)))
    # Strip duration from the dict before stamping — it's a turn count, not
    # an effect-magnitude field, and apply_effect already takes it as arg.
    stamp = {k: v for k, v in (overrides or {}).items() if k != "duration"} or None
    # ``hp_max_pct`` is a one-shot structural change, not an aggregated stat:
    # mutate ``hp_max`` directly on first application (clamp HP), then drop
    # the key from the stamped stat_bonus so it never feeds get_combat_modifiers.
    # Re-applying the same effect (refresh) is a no-op for hp_max — the cap
    # only shrinks once per fight per debuff.
    if stamp:
        sb = stamp.get("stat_bonus")
        if sb and "hp_max_pct" in sb:
            pct = float(sb["hp_max_pct"])
            sb_clean = {k: v for k, v in sb.items() if k != "hp_max_pct"}
            if not target.has_effect(effect_key) and pct != 0.0:
                new_max = max(1, int(target.hp_max * (1.0 + pct)))
                target.hp_max = new_max
                target.hp = min(target.hp, new_max)
            stamp = {**stamp, "stat_bonus": sb_clean} if sb_clean else (
                {k: v for k, v in stamp.items() if k != "stat_bonus"} or None
            )
    target.apply_effect(effect_key, dur, overrides=stamp)

    # Hồng Liên Nghiệp Hỏa reaction — every debuff/CC landing on a Hồng-Liên-
    # marked holder pushes a Nghiệp Hỏa stack and rolls a 30 % buff burn.
    # Fires for ALL debuff/CC paths (including DoTs — user spec: "dot count
    # in") so it sits BEFORE the per-stack-kind branches return below.
    _fire_nghiep_hoa_hong_lien_reaction(session, effect_key, target, actor)

    # Reactive trigger — when the target catches a non-DoT debuff/CC, fire
    # any ``cast_on_debuff_received`` skills they know back at ``actor``.
    # DoT entries (burn/bleed/shock/poison stacks + generic DoT debuffs)
    # are skipped per the skill's exclusion rule.
    if (
        meta.dot_pct == 0
        and actor is not None
        and actor is not target
        and actor.is_alive()
    ):
        _trigger_debuff_received_skills(session, defender=target, attacker=actor)

    # Reactive proc — if the actor has any skill keyed off ``proc_on_target_status``
    # matching the just-applied effect (e.g. Chung Yên's Diệt Tận on CCStun),
    # fire it once before the regular log line. Routed through ``cast_skill``
    # so damage, on-hit procs, and logging all behave like a normal cast.
    _fire_proc_on_target_status(session, actor, target, effect_key)

    # Burn stacks on every application — fire-build cornerstone
    if effect_key == EffectKey.DEBUFF_THIEU_DOT:
        if actor is not None:
            _propagate_stack_build(actor, target, "burn")
        target.add_burn_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.burn_stacks}/{effective_stack_cap(target, EffectKey.DEBUFF_THIEU_DOT.value)}] ({dur}t)"
        )
        return
    # Tam Muội Chân Hỏa stacks — same shape as burn (cap-clamped, propagates
    # build flags) but each stack also amps every fire-element DoT on the
    # target via the dot._dot_amp gate.
    if effect_key == EffectKey.DEBUFF_CHAN_HOA:
        if actor is not None:
            _propagate_stack_build(actor, target, "chan_hoa")
        target.add_chan_hoa_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.chan_hoa_stacks}/{effective_stack_cap(target, EffectKey.DEBUFF_CHAN_HOA.value)}] ({dur}t)"
        )
        return
    # Nghiệp Hỏa stacks — fed reactively by Hồng Liên (one stack per debuff
    # the holder receives). Same shape as burn so the dot pipeline reads
    # ``nghiep_hoa_stacks × nghiep_hoa_per_stack_pct``. The reactive feeder
    # lives in ``_fire_nghiep_hoa_hong_lien_reaction`` below — direct
    # applies (e.g. for testing) still go through this branch normally.
    if effect_key == EffectKey.DEBUFF_NGHIEP_HOA:
        if actor is not None:
            _propagate_stack_build(actor, target, "nghiep_hoa")
        target.add_nghiep_hoa_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.nghiep_hoa_stacks}] ({dur}t)"
        )
        return
    # U Minh stacks — pure MP-burn DoT, no HP damage. Each application bumps
    # one stack (cap 3); the per-turn MP drain runs in
    # ``CombatSession._process_periodic`` and scales with stack count.
    if effect_key == EffectKey.DEBUFF_U_MINH:
        target.add_u_minh_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.u_minh_stacks}/{effective_stack_cap(target, EffectKey.DEBUFF_U_MINH.value)}] ({dur}t)"
        )
        return
    # Hỏa Vân stacks — fire-cloud combo marker. Each evade (from either
    # side) chains a Hỏa Vân-Kiếm cast that stamps one stack. When the
    # holder reaches the auto-cast threshold (5), ``find_auto_cast_skill``
    # fires Hỏa Vân Sậu Thiên Kiếm at next turn start (which consumes the
    # stacks via ``consume_auto_cast_stacks``).
    if effect_key == EffectKey.DEBUFF_HOA_VAN:
        target.add_hoa_van_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.hoa_van_stacks}/"
            f"{effective_stack_cap(target, EffectKey.DEBUFF_HOA_VAN.value)}] ({dur}t)"
        )
        return
    # Phượng Hỏa stacks — fire DoT + per-stack heal-reduction. Reflective:
    # normally applied by ``apply_reactive_damage`` when the defender owns
    # ``BuffPhuongHoangChanHoa``; direct applies (testing / future skills)
    # still flow through this branch.
    #
    # Cap flows through ``effective_stack_cap`` (meta + per-cast override).
    # ``per_stack_pct`` is still seeded on the Combatant field because the
    # DoT pipeline in dot.py reads it via ``stack_kind`` lookup.
    if effect_key == EffectKey.DEBUFF_PHUONG_HOA:
        ovr = overrides or {}
        pct_seed = float(ovr.get("per_stack_pct", 0.0) or meta.per_stack_pct or 0.0)
        if pct_seed > target.phuong_hoa_per_stack_pct:
            target.phuong_hoa_per_stack_pct = pct_seed
        target.add_phuong_hoa_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.phuong_hoa_stacks}/"
            f"{effective_stack_cap(target, EffectKey.DEBUFF_PHUONG_HOA.value)}] ({dur}t)"
        )
        return
    # Bleed stacks — Kim playstyle mirror of burn
    if effect_key == EffectKey.DEBUFF_CHAY_MAU:
        if actor is not None:
            _propagate_stack_build(actor, target, "bleed")
        target.add_bleed_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.bleed_stacks}/{effective_stack_cap(target, EffectKey.DEBUFF_CHAY_MAU.value)}] ({dur}t)"
        )
        return
    # Shock stacks — Lôi playstyle mirror of burn
    if effect_key == EffectKey.DEBUFF_SOC_DIEN:
        if actor is not None:
            _propagate_stack_build(actor, target, "shock")
        target.add_shock_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.shock_stacks}/{effective_stack_cap(target, EffectKey.DEBUFF_SOC_DIEN.value)}] ({dur}t)"
        )
        return
    # Poison stacks — Mộc / Âm playstyle mirror of burn. Each application
    # adds a stack; tick scales with stacks × poison_per_stack_pct.
    if effect_key == EffectKey.DEBUFF_DOC_TO:
        if actor is not None:
            _propagate_stack_build(actor, target, "poison")
        target.add_poison_stack(1)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.poison_stacks}/{effective_stack_cap(target, EffectKey.DEBUFF_DOC_TO.value)}] ({dur}t)"
        )
        return
    # Cửu Khúc Hoàng Hà — formation auto-stamp. Increments stack count and
    # snapshots the *applier's* tier magnitudes onto the holder so the
    # debuff's strength tracks the formation that applied it (not the
    # holder's own — which would be zero on a fresh enemy). Snapshots take
    # the strongest seen so a lower-tier reapply can't downgrade.
    if effect_key == EffectKey.DEBUFF_CUU_KHUC:
        actor_ck = (getattr(actor, "cuu_khuc", None) or {}) if actor else {}
        per_hit = max(1, int(actor_ck.get("per_hit", 1)))
        target.add_cuu_khuc_stack(per_hit)
        if actor is not None:
            target.cuu_khuc_atk_reduce_active = max(
                target.cuu_khuc_atk_reduce_active,
                float(actor_ck.get("atk_reduce_pct", 0.0)),
            )
            target.cuu_khuc_res_shred_active = max(
                target.cuu_khuc_res_shred_active,
                float(actor_ck.get("res_shred_pct", 0.0)),
            )
        cap = effective_stack_cap(target, EffectKey.DEBUFF_CUU_KHUC.value)
        session.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
            f"[×{target.cuu_khuc_stacks}/{cap}] ({dur}t)"
        )
        return
    # Nhược Thủy Ấn — each application adds 1 mark (per-stack -res_thuy
    # expanded by ``get_combat_modifiers``). When the stack cap is reached
    # the mark detonates: damage = ``thuy_mark_detonate_lost_hp_pct ×
    # (hp_max - hp)`` of the target, then the counter resets to 0 and the
    # debuff entry is stripped so the next cast starts a fresh build-up.
    if effect_key == EffectKey.DEBUFF_NHUOC_THUY_AN:
        target.add_thuy_mark_stack(1)
        cap = effective_stack_cap(target, EffectKey.DEBUFF_NHUOC_THUY_AN.value)
        if target.thuy_mark_stacks < cap:
            session.log.append(
                f"    {meta.emoji} **{target.name}** bị **{meta.vi}** "
                f"[×{target.thuy_mark_stacks}/{cap}] ({dur}t)"
            )
            return
        # Detonation. Read the lost-HP fraction from the meta so designers
        # can tune via effects.py without code edits.
        lost_pct = float(meta.stat_bonus.get("thuy_mark_detonate_lost_hp_pct", 0.10))
        lost_hp = max(0, target.hp_max - target.hp)
        det_dmg = max(1, int(lost_hp * lost_pct))
        target.take_damage(det_dmg)
        target.consume_thuy_mark_stacks()
        # Strip the debuff entry so per-stack -res_thuy clears with the marks.
        target.effects.pop(EffectKey.DEBUFF_NHUOC_THUY_AN, None)
        target.effect_overrides.pop(EffectKey.DEBUFF_NHUOC_THUY_AN, None)
        session.log.append(
            f"    💥 **{meta.vi}** bùng nổ trên **{target.name}** "
            f"({cap} tầng) — -{det_dmg:,} HP (10% HP đã mất) | "
            f"{target.hp:,}/{target.hp_max:,} HP"
        )
        return
    # Generic DoT: propagate attacker's DoT damage boosters (bleed-mk2, etc.)
    if meta.dot_pct > 0 and actor is not None:
        _propagate_dot_bonuses(actor, target)
    session.log.append(
        f"    {meta.emoji} **{target.name}** bị **{meta.vi}** ({dur}t)"
    )


def _trigger_debuff_received_skills(
    session: "CombatSession", defender: Combatant, attacker: Combatant,
) -> None:
    """Fire any ``cast_on_debuff_received`` skills the defender knows back
    at the attacker. Each such skill fires N times where N = current count
    of DEBUFF + CC entries on the defender; afterward it strips
    ``strip_self_debuffs`` random debuffs from self.

    Reactive casts run with ``mp_cost=0`` and ``_suppress_extras=True`` so
    they're free, never set their own cooldown, and can't recursively
    trigger chain / multi-hit / summon / formation hooks. The skill
    designer is responsible for keeping ``effects: []`` (or DoT-only) so
    this path doesn't open an infinite-debuff bounce.
    """
    for skill_key in defender.skill_keys:
        data = registry.get_skill(skill_key)
        if not data or not data.get("cast_on_debuff_received"):
            continue

        debuff_count = sum(
            1 for k in defender.effects
            if (m := EFFECTS.get(k)) and m.kind in (EffectKind.DEBUFF, EffectKind.CC)
        )
        if debuff_count <= 0:
            continue

        session.log.append(
            f"  ⚔️ **{defender.name}** kích hoạt **{data.get('vi', skill_key)}** "
            f"(phản chiêu ×{debuff_count}):"
        )
        for _ in range(debuff_count):
            if not attacker.is_alive():
                break
            cast_skill(
                session, defender, attacker, skill_key, data,
                mp_cost=0, _suppress_extras=True,
            )

        strip_n = int(data.get("strip_self_debuffs", 0))
        if strip_n > 0:
            debuff_keys = [
                k for k in list(defender.effects)
                if (m := EFFECTS.get(k)) and m.kind in (EffectKind.DEBUFF, EffectKind.CC)
            ]
            if debuff_keys:
                picks = session.rng.sample(debuff_keys, min(strip_n, len(debuff_keys)))
                stripped: list[str] = []
                for k in picks:
                    defender.effects.pop(k, None)
                    defender.effect_overrides.pop(k, None)
                    m = EFFECTS.get(k)
                    stripped.append(m.vi if m else k)
                if stripped:
                    session.log.append(
                        f"    ☀️ **{defender.name}** Phục Ma rũ bỏ {len(stripped)} trạng thái: "
                        f"{', '.join(stripped)}"
                    )


def auto_attack(
    session: "CombatSession", actor: Combatant, target: Combatant
) -> None:
    """Physical auto-attack using ATK stat, reduced by target physical defense."""
    from src.game.engine.damage.physical import apply_physical_defense
    raw = max(1, int(actor.atk * session.rng.uniform(0.85, 1.15) + 5))
    dmg = apply_physical_defense(raw, "physical", target.def_stat)
    fdr = effective_damage_reduction(target, {})
    if fdr > 0:
        dmg = int(dmg * (1.0 - fdr))
    dmg = max(1, dmg)
    target.take_damage(dmg)
    session.log.append(
        f"  👊 **{actor.name}** tấn công cơ bản → "
        f"{colorize_damage(f'-{dmg} HP', None)}"
    )
