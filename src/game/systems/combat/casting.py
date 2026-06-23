"""Skill-cast pipeline.

Covers the path a single skill takes from "actor selected it" to "target took
the hit": damage calculation, effect application, support-skill buffs, and the
parallel formation barrage that multi-slot Trận Tu relies on.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.data.registry import registry
from src.game.constants.balance import MAX_PHYS_REDUCTION
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
    effective_phys_dmg_reduce, effective_stack_cap, get_combat_modifiers,
)
from src.game.systems.combatant import Combatant
from src.game.systems.skill_mastery import power_mult
from src.utils.config import settings

from .bursts import burst_mana_stacks, burst_shield
from .cast_consumers import apply_post_cast_consumers
from .dmg_riders import (
    apply_base_dmg_riders, apply_dmg_bonus_riders, apply_in_dmg_riders,
)
from .helpers import _build_skill_obj, _propagate_dot_bonuses, _propagate_stack_build
from .inflict_interceptors import dispatch_pre_stamp_interceptors
from .stack_stampers import dispatch_stack_stamp
# Side-effect import: registers the Đồng Quy Ấn apply-time snapshot stamper
# into the stack-stamper registry so it fires from ``inflict_debuff``.
from . import dong_quy as _dong_quy  # noqa: F401
from .two_phase_consumers import (
    CastContext, apply_pre_damage_consumers,
    mutate_actor_element_pen, mutate_actor_final_dmg_bonus,
    run_after_damage_callbacks,
)
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


_DIA_MACH_STACK_CAP = 5


def _bump_dia_mach_stack(
    session: "CombatSession", target: Combatant, attacker: Combatant, dmg: int,
) -> None:
    """Địa Mạch Quy Chân — incoming-damage stack pump + auto-cast at cap.

    Ownership gate: target must have ``SkillDiaMachQuyChan`` in their
    ``skill_keys``. Each non-zero hit bumps ``dia_mach_stacks`` toward
    cap 5; while stacks > 0, ``build_attack_stats`` folds
    ``def_stat × 10% × stacks`` into both ``effective_atk`` and
    ``effective_matk`` (armor-to-power conversion).

    At cap, the stacks are spent immediately: reset to 0 and fire
    ``SkillDaiDiaMaiTang`` back at the attacker as a free retaliation
    (``mp_cost=0``, ``_suppress_extras=True`` so chain/multi-hit hooks
    on the burial skill don't recurse).
    """
    if dmg <= 0:
        return
    if "SkillDiaMachQuyChan" not in target.skill_keys:
        return
    if target.dia_mach_stacks >= _DIA_MACH_STACK_CAP:
        return
    target.dia_mach_stacks += 1
    session.log.append(
        f"    🌍 **{target.name}** Địa Mạch Quy Chân "
        f"[×{target.dia_mach_stacks}/{_DIA_MACH_STACK_CAP}] "
        f"(+{int(target.def_stat * 0.10 * target.dia_mach_stacks):,} ATK/MATK chuyển hoá)"
    )
    if target.dia_mach_stacks < _DIA_MACH_STACK_CAP:
        return
    # Cap reached — spend the stacks via Đại Địa Mai Táng auto-retaliation.
    burial = registry.get_skill("SkillDaiDiaMaiTang")
    if burial is None or not attacker.is_alive():
        # Skill missing or attacker already dead — just reset and bail.
        target.dia_mach_stacks = 0
        return
    session.log.append(
        f"  🪦 **{target.name}** Đại Địa Mai Táng kích hoạt! "
        f"(tiêu hao {_DIA_MACH_STACK_CAP} tầng Địa Mạch)"
    )
    target.dia_mach_stacks = 0
    cast_skill(
        session, target, attacker, "SkillDaiDiaMaiTang", burial,
        mp_cost=0, _suppress_extras=True,
    )


def cast_skill(
    session: "CombatSession", actor: Combatant, target: Combatant,
    skill_key: str, skill_data: dict, mp_cost: int,
    _suppress_extras: bool = False,
    _hit_index: int = 0,
    _chain_landed: list[bool] | None = None,
) -> None:
    """Execute one skill cast — MP spend, damage pipeline or support
    effects, on-hit procs, and cooldown. Extracted so the main rotation
    and parallel formation firing share the same machinery.

    ``_suppress_extras`` is set internally for multi-hit follow-ups and
    chained casts so per-cast hooks (cooldown, charge counter, chain skill,
    summon spawn) only fire once per logical cast.

    ``per_hit_specs`` on skill_data enables a per-hit chain — index N of
    the array is merged onto skill_data for the Nth hit, so each strike
    can apply its own effects / crit bonus / mp drain / label. ``_hit_index``
    threads the current hit through replays; ``_chain_landed`` collects
    per-hit landed status so a terminal-hit ``chain_finisher`` can fire only
    when all prior hits connected.
    """
    actor.mp = max(0, actor.mp - mp_cost)
    # ── Skill Mastery power multiplier (Strategy B) ────────────────────────
    # The ONLY place the flag + player gate is evaluated. A mastered PLAYER
    # cast stamps ``mastery_mult`` (= power_mult(level)) onto every effect it
    # applies; each magnitude read site multiplies by the stamp. Enemies carry
    # an empty ``skill_mastery`` and the ``actor.key == "player"`` gate is a
    # second safety, so non-player and flag-off casts resolve to 1.0 → inert.
    mastery_mult = 1.0
    if settings.skill_mastery_enabled and actor.key == "player":
        level = actor.skill_mastery.get(skill_key, 0)
        if level:
            mastery_mult = power_mult(level)
    # ── Same-element cast streak ───────────────────────────────────────────
    # Bumped on top-level casts only (``_suppress_extras`` gates out reactive
    # / 0-mp follow-ups, matching the ``skill_usage_count`` discipline) so a
    # chain of same-element skills builds the streak while a cross-element or
    # auto-fired cast doesn't. Incremented BEFORE the damage roll so this
    # cast's own rider (``same_element_streak_scaling``) reads the streak
    # value INCLUDING this cast: first Lôi cast → streak 1 (no bonus), fifth
    # consecutive Lôi cast → streak 5.
    if not _suppress_extras:
        _cast_elem = skill_data.get("element")
        if _cast_elem and _cast_elem == actor.last_cast_element:
            actor.same_element_streak += 1
        else:
            actor.same_element_streak = 1 if _cast_elem else 0
            actor.last_cast_element = _cast_elem
        # Truy Kích Liên Vũ (Phong B1) — self-combo escalator. Colocated with
        # the same-element streak so it shares the top-level-cast gate.
        # Casting the combo skill itself increments the counter (capped) and
        # the riders read the post-increment value for THIS cast. Casting any
        # OTHER-element skill breaks the chain (reset 0); a same-element
        # non-combo Phong cast leaves the counter untouched (only the combo
        # skill grows it). CC-taken breaks live in ``inflict_debuff``.
        _combo_spec = skill_data.get("combo_counter_scaling")
        if _combo_spec:
            _combo_field = _combo_spec.get("stack_field", "phong_combo")
            _combo_cap = int(_combo_spec.get("combo_cap", 0))
            _cur = int(getattr(actor, _combo_field, 0))
            _nxt = _cur + 1
            if _combo_cap > 0:
                _nxt = min(_nxt, _combo_cap)
            setattr(actor, _combo_field, _nxt)
        elif _cast_elem and _cast_elem != "phong" \
                and _combo_spec is None and actor.phong_combo > 0:
            # A different-element top-level cast snaps the Liên Vũ chain. A
            # plain Phong cast that ISN'T the combo skill is left alone so the
            # player can interleave Phong utility without losing the streak,
            # matching ``break_on_other_element`` (other ELEMENT, not skill).
            actor.phong_combo = 0
    # ── Per-hit chain init / spec merge ────────────────────────────────────
    # Top-level cast of a per_hit_specs skill seeds the landed tracker so
    # every replay can append to the same list. Replays inherit the list
    # via _chain_landed kwarg; we never reset it mid-chain.
    per_hit_specs = skill_data.get("per_hit_specs")
    if per_hit_specs and not _suppress_extras and _chain_landed is None:
        _chain_landed = []
    # Merge this hit's spec onto skill_data (label/effects/effect_overrides/
    # crit_rating_bonus/drain_target_mp). per_hit_specs itself is preserved
    # so replays can keep looking up future specs.
    if per_hit_specs and _hit_index < len(per_hit_specs):
        _spec = per_hit_specs[_hit_index]
        skill_data = {**skill_data, **{k: v for k, v in _spec.items() if k != "label"},
                      "per_hit_specs": per_hit_specs}
        _label = _spec.get("label")
        if _label:
            session.log.append(f"    ⚜️ *Thương {_hit_index + 1}* — **{_label}**")
    base_dmg = skill_data.get("base_dmg", 0)
    # Per-formation gem scaling on passive echo skills — only the matching
    # ``per_hit_followup`` skill picks up its formation's threshold-fed
    # damage bonus, so standard skill casts never see the multiplier.
    if base_dmg > 0 and skill_data.get("per_hit_followup"):
        bonus = float(getattr(actor, "formation_skill_dmg_bonus", 0.0) or 0.0)
        if bonus > 0:
            base_dmg = max(1, int(base_dmg * (1.0 + bonus)))

    # Phù Dao dive — first attack-skill cast while ``BuffPhuDao`` is active
    # spends every accumulated altitude tier as bonus base_dmg, and the
    # buff/altitude are stripped after the skill resolves (whether or not
    # the strike connects — "you dove"). Suppressed multi-hit follow-ups
    # inherit the already-amped base_dmg without re-rolling the rider, so
    # only the top-level cast triggers the dive.
    _phu_dao_dive: dict | None = None
    if (
        not _suppress_extras
        and base_dmg > 0
        and skill_data.get("category") == "attack"
        and actor.has_effect("BuffPhuDao")
        and actor.phu_dao_altitude > 0
    ):
        _pd_ovr = actor.effect_overrides.get("BuffPhuDao", {}) or {}
        _pd_alt = int(actor.phu_dao_altitude)
        _pd_per_stack = float(_pd_ovr.get("per_stack_pct", 0.10))
        _pd_mult = 1.0 + _pd_alt * _pd_per_stack
        amped = max(1, int(base_dmg * _pd_mult))
        bonus = amped - base_dmg
        base_dmg = amped
        skill_data = {**skill_data, "base_dmg": base_dmg}
        _phu_dao_dive = {
            "altitude": _pd_alt,
            "rider_key": _pd_ovr.get("rider_on_descent"),
            "rider_chance": float(_pd_ovr.get("rider_chance_per_stack", 0.0)) * _pd_alt,
        }
        session.log.append(
            f"    🪁 **{actor.name}** **Đại Bằng Phá Vân Trảm** "
            f"— +{bonus:,} ST (Cao Độ ×{_pd_alt})"
        )
    dealt_total = 0
    # Phi Thiên Lăng Vân — L9 unevadable-arm consume flag. Set inside the
    # damage block when this top-level cast spends the arm; read by the
    # end-of-cast cadence block. Initialized here so support / 0-dmg casts
    # (which skip the damage block) still resolve it cleanly.
    _phong_bypass_this_cast = False
    # Cast context for the generic chain-skill cast_on triggers (crit / kill
    # / shield_break). Filled inside the damage block below; consumed by
    # ``cast_chain_skill`` at the bottom. Stays empty for support / 0-dmg
    # casts so triggers like ``crit`` / ``kill`` correctly never fire.
    cast_ctx: dict = {"is_crit": False, "killed": False, "shield_broken": False}

    actor_mods = get_combat_modifiers(actor)
    target_mods = get_combat_modifiers(target)

    # Pre-pipeline ``base_dmg`` rider registry — every per-skill additive
    # bonus that folds into the base damage *before* crit/element/final-bonus
    # lives in ``dmg_riders.py`` as a ``@register_base_dmg_rider`` function.
    # Built-ins covered today: anti-regen (``bonus_dmg_per_target_regen_*``),
    # missing-HP flat (``bonus_dmg_per_target_hp_lost_pct``), and caster-stat
    # scaling (``base_dmg_caster_scaling``). Adding a new rider is one
    # decorated function — no edits to cast_skill required.
    base_dmg, skill_data = apply_base_dmg_riders(
        skill_data, actor, target, base_dmg, actor_mods, session.log,
    )

    if base_dmg > 0:
        # Blind miss check (Âm DebuffLoaMat) — happens before evasion roll
        # so a blinded attacker can whiff regardless of target's evasion.
        # MP is already spent above; matches DebuffTeLiet's "wasted swing" feel.
        if check_attack_miss(actor, session.rng):
            session.log.append(
                f"  🌫️ **{actor.name}** dùng *{skill_data['vi']}* → đánh trượt do **Lóa Mắt**!"
            )
            apply_skill_effects(
                session, skill_data, actor, target, hit=False,
                mastery_mult=mastery_mult,
            )
            actor.set_cooldown(skill_key, skill_data.get("cooldown", 1))
            return

        # Per-hit crit_rating bonus — Lục Thần Thương's Xuyên Tâm strike.
        # Folds into actor_mods so build_attack_stats picks it up for THIS
        # cast only (replays running their own cast_skill re-derive from
        # their own per_hit_specs entry — so the bonus stays hit-scoped).
        _hit_crit_rating = int(skill_data.get("crit_rating_bonus", 0))
        if _hit_crit_rating:
            actor_mods["crit_rating"] = (
                actor_mods.get("crit_rating", 0) + _hit_crit_rating
            )

        # Pre-damage ``final_dmg_bonus`` rider registry — every per-skill
        # bonus that folds into ``actor_mods["final_dmg_bonus"]`` lives in
        # ``dmg_riders.py`` as a ``@register_dmg_bonus_rider`` function.
        # Built-ins covered today: ``dmg_per_target_buff_pct`` (Thẩm Phán),
        # ``final_dmg_bonus_per_target_hp_lost_bucket`` (Bạch Hổ Hành Hung),
        # ``final_dmg_bonus_per_target_debuff_count`` (Phá Lãng), and
        # ``seal_refresh_dmg_bonus`` (Sơn Hà Áp). Adding a new rider is one
        # decorated function — no edits to cast_skill required.
        apply_dmg_bonus_riders(skill_data, actor, target, actor_mods, session.log)

        # Chân Hỏa Phần Thiên (L6) — chance-gated burning amp. Per hit vs a
        # burning target, roll ``hoa_burning_amp_chance``; on success fold
        # ``hoa_burning_amp_pct`` into THIS cast's ``final_dmg_bonus`` (the
        # ``actor_mods`` dict is rebuilt per cast_skill call, so each hit —
        # including multi-hit replays and the phoenix burst — rolls
        # independently). Distinct from the always-on ``bonus_dmg_vs_burn``
        # lane (left at 0 for this body). Inert when the chance is 0 / the
        # target isn't burning.
        if (
            actor.hoa_burning_amp_chance > 0
            and target.burn_stacks > 0
            and session.rng.random() < actor.hoa_burning_amp_chance
        ):
            actor_mods["final_dmg_bonus"] = (
                actor_mods.get("final_dmg_bonus", 0.0) + actor.hoa_burning_amp_pct
            )
            session.log.append(
                f"    ☀️ **{actor.name}** Chân Hỏa Phần Thiên "
                f"— +{int(actor.hoa_burning_amp_pct * 100)}% ST (địch đang cháy)"
            )

        # Skill element captured up-front — used by the pen helpers and
        # the CastContext bridge to the two-phase consumer registry.
        skill_elem = skill_data.get("element")

        # ── CastContext ──────────────────────────────────────────────────
        # Threaded state for the two-phase consumer registry AND the
        # inline pre-roll mutations below (element_pen_self, passive_pen,
        # target_stack_scale). The ``mutate_actor_*`` helpers use "first
        # writer captures" semantics on ``ctx.prev_pen`` /
        # ``ctx.prev_crit_dmg_rating`` so multiple riders layering bumps
        # on the same field compose correctly — the damage-roll ``finally``
        # restores back to the original regardless of how many mutations
        # happened. ``ctx.base_dmg`` / ``ctx.skill_data`` are read back
        # below so consumers that grow base damage (Canh Kim) take effect.
        ctx = CastContext(
            actor=actor, target=target, session=session,
            skill_element=skill_elem, skill_data=skill_data,
            actor_mods=actor_mods, base_dmg=base_dmg,
        )

        # ``element_pen_self`` and ``scaling_per_target_stack`` riders moved
        # to ``two_phase_consumers`` (data-driven, registered). They run
        # below via ``apply_pre_damage_consumers(ctx)``.

        # Passive pen bonus — Dung Linh Chân Quyết-style threshold pen.
        # Scans the actor's owned passives for ``passive_pen_at_elem_dot_count``
        # entries whose element matches the current skill's element AND whose
        # opponent-DoT-count threshold is met (inclusive ``count >= threshold``);
        # sum the pen contributions additively. Folds into the same temporary
        # ``actor.element_pen`` slot so the ``finally`` restore clears both
        # sources cleanly via ``ctx.prev_pen``.
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
                mutate_actor_element_pen(ctx, passive_pen_total)

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

        # ``scaling_per_target_stack`` rider moved to ``two_phase_consumers``.
        # The post-damage ``dmg_pct`` multiplier path (still inline below)
        # reads from ``target_stack_scale`` / ``target_stack_count`` via the
        # ctx-local snapshot.
        target_stack_scale = skill_data.get("scaling_per_target_stack")
        target_stack_count = 0
        if target_stack_scale:
            _sk_kind = target_stack_scale.get("stack")
            if _sk_kind:
                target_stack_count = int(getattr(target, f"{_sk_kind}_stacks", 0))

        # Bôn Lôi Thuật — Sốc Điện stack consumer. When the bộ pháp is active
        # and a Lôi skill is being cast on a target carrying ≥1 Sốc Điện
        # stack: devour 1 stack, amp this cast's ``final_dmg_bonus`` by
        # ``bon_loi_consume_amp`` (default +15%). Gated on top-level casts
        # only (``_suppress_extras=False``) so multi-hit chains burn a
        # single stack per logical cast, not per sub-hit. The damage-roll
        # ``finally`` restores ``final_dmg_bonus`` via ``ctx.prev_*``.
        if (
            not _suppress_extras
            and skill_elem == "loi"
            and actor.has_effect(EffectKey.BUFF_BON_LOI_THUAT)
            and target.shock_stacks > 0
        ):
            bl_meta = EFFECTS[EffectKey.BUFF_BON_LOI_THUAT]
            amp = float(bl_meta.stat_bonus.get("bon_loi_consume_amp", 0.15))
            target.shock_stacks -= 1
            mutate_actor_final_dmg_bonus(ctx, amp)
            session.log.append(
                f"    ⚡ **{actor.name}** Bôn Lôi Thuật nuốt 1 tầng Sốc Điện "
                f"→ +{int(amp * 100)}% ST cú này"
            )

        # ── Two-phase consumer registry ──────────────────────────────────
        # Every per-skill hook that needs to coordinate state across the
        # damage roll lives in ``two_phase_consumers.py`` as a
        # ``@register_pre_damage_consumer`` function returning an optional
        # post-damage closure. Built-ins today:
        # ``consume_target_marks_for_execute`` (Thất Sát execute) and
        # ``consume_target_bleed_on_cast`` (Canh Kim bleed eat).
        # ``after_callbacks`` is fired post-damage by
        # ``run_after_damage_callbacks`` below.
        after_callbacks = apply_pre_damage_consumers(ctx)
        base_dmg = ctx.base_dmg
        skill_data = ctx.skill_data

        try:
            # Phi Thiên Lăng Vân — L9 unevadable cadence: armed cast bypasses
            # evasion entirely (inject bypass_evasion=True into the skill obj).
            if not _suppress_extras and actor.phong_unevadable_armed:
                _phong_bypass_this_cast = True
                skill_data = {**skill_data, "bypass_evasion": True}
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
            # Hỗn Nguyên Vô Cực — Vạn Pháp Vô Cản. A per-cast chance to treat the
            # target's elemental resistance as 0% (full elemental penetration for
            # this hit). Inert for every other build (chance 0.0).
            if (
                actor.omni_res_ignore_chance > 0
                and session.rng.random() < actor.omni_res_ignore_chance
            ):
                pen_pct = 1.0
                session.log.append(
                    f"  ♾️ **{actor.name}** Vạn Pháp Vô Cản — xuyên thủng kháng tính địch!"
                )
            result = calculate_damage(skill_obj, attack_stats, defense_stats, session.rng, pen_pct)
        finally:
            # Restore actor stats mutated pre-roll by element_pen_self,
            # passive_pen, target_stack_scale, and registry consumers.
            # ``ctx.prev_*`` captures the FIRST writer's pre-mutation value
            # via the ``mutate_actor_*`` helpers, so a single restore here
            # unwinds all layered bumps regardless of order or count.
            if ctx.prev_pen is not None:
                actor.element_pen[skill_elem] = ctx.prev_pen
            if ctx.prev_crit_dmg_rating is not None:
                actor.crit_dmg_rating = ctx.prev_crit_dmg_rating
            if ctx.prev_final_dmg_bonus is not None:
                actor.final_dmg_bonus = ctx.prev_final_dmg_bonus
        dmg = result.final
        crit_tag = " 💥BẠO KÍCH!" if result.is_crit else ""

        # Per-hit chain — record landed status for the terminal finisher
        # (e.g. Lục Thần Thương's Thần Diệt true-damage burst requires the
        # 5 prior strikes to all land before firing).
        if _chain_landed is not None:
            _chain_landed.append(not result.is_evaded)

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
            # Phi Thiên Lăng Vân — L1 Phong Vân stack on dodge (+1, cap 6).
            if target.phong_van_dodge_stack:
                if target.phong_van_stacks < 6:
                    target.phong_van_stacks += 1
                    session.log.append(
                        f"    🍃 **{target.name}** Phong Vân "
                        f"[×{target.phong_van_stacks}/6]"
                    )
            # Phi Thiên Lăng Vân — L6 arm guaranteed crit on next cast.
            if target.phong_dodge_arms_crit:
                target.phong_crit_armed = True
            # Huyền Minh Nhược — L6 Uyên (Abyss Depth) on dodge: each successful
            # evade deepens the abyss (+1, cap ``hm_uyen_cap``), folding into
            # evasion via BuffHuyenMinhHuTinh's scaling_rule (stack:hm_uyen).
            if target.hm_uyen_cap > 0 and target.hm_uyen_stacks < target.hm_uyen_cap:
                target.hm_uyen_stacks += 1
                session.log.append(
                    f"    🌑 **{target.name}** Uyên "
                    f"[×{target.hm_uyen_stacks}/{target.hm_uyen_cap}]"
                )

            # Data-driven on-evade reactives — any active buff on the
            # defender carrying an ``evade_react`` block (or the legacy
            # ``proc_on_holder_evade_cast`` field) runs through the
            # capability dispatcher: counter damage → debuff inflict →
            # self-duration extend → proc-cast skill. Covers Lôi Quang
            # Điện Ảnh, Ma Long Xuất Uyên, Lưu Quang Huyễn Ảnh, and any
            # future entrant declared purely in JSON.
            from .evade_reactive import apply_evade_reactives
            apply_evade_reactives(session, actor, target)
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
                # Thiên Lôi Cường L6 — Điện Quang Phản Ứng: the DEFENDER (if it
                # carries the Lôi body) fires a bonus shock at the attacker on a
                # successful dodge. Gated inside ``not _suppress_extras`` so the
                # bonus cast (itself _suppress_extras=True) can't re-trigger it.
                if target.loi_reflex_bonus_attack and actor.is_alive():
                    _loi_bonus = registry.get_skill("SkillLoiBonusShock")
                    if _loi_bonus is not None:
                        session.log.append(
                            f"  ⚡ **{target.name}** Điện Quang Phản Ứng — "
                            f"Sốc Điện phản né!"
                        )
                        cast_skill(
                            session, target, actor,
                            "SkillLoiBonusShock", _loi_bonus, 0,
                            _suppress_extras=True,
                        )
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
            # Element-specific defender-side amp — additive with the generic
            # ``final_dmg_taken_bonus`` so an effect can stamp either lane.
            # Used by DebuffPhongNhanThuc to amp incoming Phong damage only;
            # follows the same naming convention as outgoing ``dmg_bonus_<elem>``.
            _elem_for_taken = skill_data.get("element")
            if _elem_for_taken:
                taken_bonus += float(
                    target_mods.get(f"dmg_taken_bonus_{_elem_for_taken}", 0.0)
                )
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

            # In-damage multiplier rider registry — every per-skill amp
            # that multiplies ``dmg`` *after* the pipeline lives in
            # ``dmg_riders.py`` as a ``@register_in_dmg_rider`` function.
            # Built-ins covered today: shield-piercer (``bonus_dmg_vs_shielded``),
            # Phá Phủ HP-above (``bonus_dmg_vs_hp_above``), executioner
            # HP-below (``bonus_dmg_vs_hp_below``), Liệt Diễm fire-DoT-count
            # (``bonus_dmg_per_fire_dot_pct``). Adding a new rider is one
            # decorated function — no edits to cast_skill required.
            dmg = apply_in_dmg_riders(skill_data, actor, target, dmg, session.log)

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

            # Bàn Thạch Kim Thân — per-hit damage ceiling. Aggregates any
            # active effect on the target carrying ``max_dmg_per_hit_pct_hp_max``
            # in its stat_bonus (the sum is treated as the cap fraction,
            # clamped to 0.20 so designers stacking multiple cap sources
            # can't invert the intent into an effective floor). Applies
            # AFTER all pipeline mitigation (DR/res/elem) but BEFORE the
            # shield-bypass split — so capped damage flows naturally into
            # the standard shield/HP path.
            _cap_pct = float(target_mods.get("max_dmg_per_hit_pct_hp_max", 0.0))
            if _cap_pct > 0 and target.hp_max > 0 and dmg > 0:
                _cap_pct = min(0.20, _cap_pct)
                _cap_val = max(1, int(target.hp_max * _cap_pct))
                if dmg > _cap_val:
                    _absorbed = dmg - _cap_val
                    dmg = _cap_val
                    session.log.append(
                        f"    🗿 **{target.name}** Bàn Thạch Kim Thân — "
                        f"hấp thụ {_absorbed:,} ST (giới hạn {int(_cap_pct * 100)}% HP tối đa/đòn)"
                    )

            # Kim Cang Bất Hoại — L3 Kim Thân Hộ Pháp: roll to fully negate a
            # physical hit. Chance depends on how full the defender's shield
            # is relative to gate. Inert whenever either chance field is 0.
            _attack_type = skill_data.get("attack_type", "magical")
            if (
                _attack_type == "physical"
                and target.tho_phys_immune_chance > 0
                and dmg > 0
            ):
                _gate = target.tho_phys_immune_shield_gate
                _high_chance = target.tho_phys_immune_high_shield_chance
                _cap = target.shield_cap()
                if _cap > 0 and (target.shield / _cap) > _gate and _high_chance > 0:
                    _negate_chance = _high_chance
                else:
                    _negate_chance = target.tho_phys_immune_chance
                if session.rng.random() < _negate_chance:
                    dmg = 0
                    session.log.append(
                        f"    💛 **{target.name}** Kim Thân Hộ Pháp — "
                        f"vô hiệu đòn Vật Lý!"
                    )

            # Physical-only reduction lane. Multiplies AFTER all pipeline
            # mitigation + the Kim Thân negate, BEFORE the shield split, gated on
            # physical attack_type so magical/true bypass it. Capped at
            # ``MAX_PHYS_REDUCTION`` (armor cap); a separate multiplicative lane
            # from generic ``final_dmg_reduce``.
            # Shared by Huyền Minh Nhược (L1, permanent field) and Liệt Diễm Phần
            # Thiên (L9 Hỏa Thần avatar, a temporary buff) — ``effective_phys_dmg_reduce``
            # reads BOTH the field and active-effect metas, so the avatar's window works.
            if _attack_type == "physical" and dmg > 0:
                _pr = min(MAX_PHYS_REDUCTION, effective_phys_dmg_reduce(target))
                if _pr > 0:
                    _pr_before = dmg
                    dmg = max(1, int(dmg * (1.0 - _pr)))
                    if _pr_before > dmg:
                        session.log.append(
                            f"    🛡️ **{target.name}** giảm {int(_pr * 100)}% ST Vật Lý"
                        )

            # Quy Khư Thôn Hải (Thiên Thủy Thánh L9) — at full Tịnh Hóa the abyss
            # swallows the next hit (ANY element): reduce by ``tt_abyss_reduce_pct``
            # (never to 0 — a throttled soak, not an immunity), heal the swallowed
            # portion (→ _apply_heal fires the L6 cleanse + Tịnh Hóa MP), reflect a
            # fraction back, then spend all Tịnh Hóa. Fires BEFORE the shield split
            # so the soak is on the full hit. Resets stacks → self-limits to one
            # swallow per recharge. Inert unless the holder is charged.
            if (
                target.tt_abyss_threshold > 0
                and target.tt_tinh_hoa_stacks >= target.tt_abyss_threshold
                and dmg > 0
            ):
                swallowed = int(dmg * target.tt_abyss_reduce_pct)
                if swallowed > 0:
                    dmg = max(1, dmg - swallowed)
                    target.tt_tinh_hoa_stacks = 0  # spend the charge
                    if target.tt_abyss_reflect_pct > 0 and actor.is_alive():
                        reflected = int(swallowed * target.tt_abyss_reflect_pct)
                        if reflected > 0:
                            actor.take_damage(reflected)
                            session.log.append(
                                f"    🌀 **{target.name}** Quy Khư Thôn Hải phản → "
                                f"**{actor.name}** "
                                f"{colorize_damage(f'-{reflected:,} HP', 'thuy')}"
                            )
                    healed = session._apply_heal(target, swallowed)
                    session.log.append(
                        f"    🌀 **{target.name}** Quy Khư Thôn Hải — nuốt "
                        f"{swallowed:,} ST, hồi {healed:,} HP"
                    )

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
            # Thiên Lôi Cường L9 — Lôi Điện Hóa Thần: deal +loi_bonus_true_dmg_pct
            # of a Lôi-element hit's damage as TRUE damage (straight to HP, pierces
            # resistance). Inert unless skill is Lôi AND the actor unlocked L9.
            if (
                skill_elem == "loi" and actor.loi_bonus_true_dmg_pct > 0
                and dmg > 0 and target.is_alive()
            ):
                _loi_true = int(dmg * actor.loi_bonus_true_dmg_pct)
                if _loi_true > 0:
                    target.take_damage(_loi_true, bypass_shield=True)
                    session.log.append(
                        f"    🌐 Lôi Điện Hóa Thần — +{_loi_true:,} Sát Thương Chuẩn (xuyên kháng)"
                    )
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

            # Địa Mạch Quy Chân — passive on the holder pumps a stack every
            # time they take damage from an enemy. At cap (5 stacks), spends
            # the stacks and auto-fires Đại Địa Mai Táng back at the attacker.
            # Skipped on self-damage / utility (dmg <= 0) and when the holder
            # doesn't own the passive (check happens inside the helper).
            _bump_dia_mach_stack(session, target, actor, dmg)

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

            run_on_hit_procs(session, actor, target, is_crit=result.is_crit, skill_key=skill_key)

            # Huyền Minh Nhược Thể — L9 Hắc Thủy Diệt Thế: per-CAST guaranteed
            # corrosion. ``not _suppress_extras`` gates out multi-hit replays so a
            # multi-hit skill stamps the DoTs exactly once per logical cast (not
            # per hit). On a landed damaging hit the holder applies poison + bleed;
            # when the target then carries BOTH, the Hủ Thủy Ấn mark (DoT +50% /
            # heal −40%) is applied/refreshed. Self-gates on the L9 flag.
            if (
                actor.hm_corrode_poison_stacks > 0
                and not _suppress_extras
                and target.is_alive()
                and dmg > 0
            ):
                if not target.poison_immunity:
                    target.apply_effect(
                        EffectKey.DEBUFF_DOC_TO,
                        default_duration(EffectKey.DEBUFF_DOC_TO),
                    )
                    _propagate_stack_build(actor, target, "poison")
                    for _ in range(actor.hm_corrode_poison_stacks):
                        target.add_stack("poison", 1)
                target.apply_effect(
                    EffectKey.DEBUFF_CHAY_MAU,
                    default_duration(EffectKey.DEBUFF_CHAY_MAU),
                )
                _propagate_stack_build(actor, target, "bleed")
                for _ in range(actor.hm_corrode_bleed_stacks):
                    target.add_stack("bleed", 1)
                session.log.append(
                    f"    🌑 **{actor.name}** Hắc Thủy Diệt Thế → "
                    f"Độc ×{target.poison_stacks} + Chảy Máu ×{target.bleed_stacks}"
                )
                if target.poison_stacks > 0 and target.bleed_stacks > 0:
                    _huthuy = EFFECTS.get("DebuffHuThuyAn")
                    if _huthuy is not None:
                        inflict_debuff(
                            session, "DebuffHuThuyAn", _huthuy, target, actor=actor,
                        )

            # Element-gated stack-on-hit — every successful damaging hit whose
            # element matches an equipped skill's ``passive_stack_on_element_hit``
            # entry stacks the named kind on the target. Scans both
            # ``skill_keys`` and ``formation_skill_keys`` so the trigger can
            # live on a regular skill or a formation_skill. Stacks ALL matching
            # specs (so one cast could simultaneously stack bleed and sat_an
            # if the player has both Canh Kim and Thất Sát formations active).
            # Per-stack cap from the matched effect meta naturally clamps via
            # ``add_stack`` → ``effective_stack_cap``.
            if skill_elem and target.is_alive() and dmg > 0 and not _suppress_extras:
                from src.game.systems.combatant import _STACK_EFFECT_KEY
                for _sk in (*actor.skill_keys, *actor.formation_skill_keys):
                    _pdata = registry.get_skill(_sk)
                    if not _pdata:
                        continue
                    _spec = _pdata.get("passive_stack_on_element_hit")
                    if not _spec or _spec.get("element") != skill_elem:
                        continue
                    _stack_kind = _spec.get("stack", "bleed")
                    _chance = float(_spec.get("chance", 1.0))
                    if _chance < 1.0 and session.rng.random() >= _chance:
                        continue
                    _propagate_stack_build(actor, target, _stack_kind)
                    gained = target.add_stack(_stack_kind, 1)
                    if gained > 0:
                        _meta_key = _STACK_EFFECT_KEY.get(_stack_kind)
                        cap = effective_stack_cap(target, _meta_key) if _meta_key else 0
                        _cur = getattr(target, f"{_stack_kind}_stacks", 0)
                        _meta = EFFECTS.get(_meta_key) if _meta_key else None
                        _emoji = (_meta.emoji if _meta else "🔻")
                        _name = (_meta.vi if _meta else _stack_kind)
                        session.log.append(
                            f"    {_emoji} *{_pdata.get('vi', _sk)}* — "
                            f"{_name} [×{_cur}/{cap}]"
                        )

            # Two-phase consumer post-damage phase — fires the closures
            # collected by ``apply_pre_damage_consumers`` earlier. Today:
            # Thất Sát execute true-damage + Canh Kim bleed stack drain.
            # Order matches consumer registration in two_phase_consumers.py.
            # (Note: this replaces a duplicated inline bleed zero-out that
            # appeared twice in the legacy flow — once before mark-execute
            # and once after. The second call was a no-op since stacks
            # were already drained by the first; the registry runs each
            # callback exactly once.)
            run_after_damage_callbacks(after_callbacks, ctx, dmg)

            # Cửu Khúc Hoàng Hà — formation auto-stamp. Every successful
            # damaging skill hit applies N marks (N = ``actor.cuu_khuc["per_hit"]``,
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
            # ``actor.cuu_khuc["followup_skill_key"]`` (default
            # ``SkillAtkThuyLongDan_R7``), so different formations can
            # declare different chain targets. Power scales with
            # ``actor.cuu_khuc["followup_pct"]`` (0.40 base, 1.0 at gem 10).
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
            apply_reactive_damage(session, actor, target, dmg, skill_element=skill_elem)

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

            # Thái Bạch periodic guaranteed-crit — the arm is single-use: a
            # landed damaging hit spends it so only THIS swing auto-crits. Reset
            # here (after on-hit procs, mirroring the Đông Băng consume) so any
            # "vs frozen"-style readers still saw force_crit during the hit. The
            # ``> 0`` interval gate keeps this inert for every non-bleed-hunter
            # actor (default field is False anyway).
            if actor.bleed_hunter_crit_armed:
                actor.bleed_hunter_crit_armed = False
            # Hoàng Cổ Thánh Thể L6 — single-use arm consumed on first landed hit.
            if actor.saint_crit_armed:
                actor.saint_crit_armed = False
            # Phi Thiên Lăng Vân L6 — consume dodge-armed crit on first landed hit.
            if actor.phong_crit_armed:
                actor.phong_crit_armed = False
                # L6 Phi Thiên Hư Ảnh: armed crit → apply DebuffAnPhong to target.
                if actor.phong_dodge_crit_applies_an_phong and result.is_crit:
                    _an_phong_meta = EFFECTS.get(EffectKey.DEBUFF_AN_PHONG)
                    if _an_phong_meta is not None and target.is_alive():
                        inflict_debuff(
                            session, EffectKey.DEBUFF_AN_PHONG,
                            _an_phong_meta, target, actor=actor,
                        )
                        session.log.append(
                            f"    🌫️ **{actor.name}** Phi Thiên Hư Ảnh "
                            f"— áp Ấn Phong lên **{target.name}**!"
                        )
            # Phi Thiên Lăng Vân L9 — consume unevadable arm after this cast.
            # The cadence counter reset + skipped-increment happens at the
            # end-of-cast cadence block (gated on ``_phong_bypass_this_cast``)
            # so the NEXT unevadable lands exactly ``interval`` casts later.
            if _phong_bypass_this_cast:
                actor.phong_unevadable_armed = False
            # Phi Thiên Lăng Vân L9 — on crit, 50% Cuốn Bay.
            if (
                result.is_crit
                and actor.phong_cuon_bay_on_crit_chance > 0
                and target.is_alive()
                and not target.immune_hard_cc
                and session.rng.random() < actor.phong_cuon_bay_on_crit_chance
            ):
                _cuon_bay_meta = EFFECTS.get("DebuffCuonBay")
                if _cuon_bay_meta is not None:
                    inflict_debuff(
                        session, "DebuffCuonBay", _cuon_bay_meta, target, actor=actor,
                    )
                    session.log.append(
                        f"    💨 **{actor.name}** Thiên Phong Vô Ảnh "
                        f"— Cuốn Bay **{target.name}**!"
                    )

            # Per-hit MP drain — Lục Thần Thương's Đoạn Linh strike rips a
            # fixed chunk of MP from the target on hit. ``drain_target_mp``
            # is a flat amount; ``drain_target_mp_pct`` is a fraction of
            # ``target.mp_max`` (Tử Tiêu Thần Lôi style). Both sources add
            # together when both are present. No-op when the spec is absent
            # or the hit evaded (gated on landed status above via the
            # result.is_evaded flag).
            _mp_drain_flat = int(skill_data.get("drain_target_mp", 0))
            _mp_drain_pct = float(skill_data.get("drain_target_mp_pct", 0.0))
            _mp_drain = _mp_drain_flat + int(target.mp_max * _mp_drain_pct)
            if _mp_drain > 0 and not result.is_evaded:
                before = target.mp
                target.mp = max(0, target.mp - _mp_drain)
                drained = before - target.mp
                if drained > 0:
                    session.log.append(
                        f"    💧 **{actor.name}** Đoạn Linh — **{target.name}** "
                        f"-{drained:,} MP ({target.mp:,}/{target.mp_max:,})"
                    )

            # Damage-scaled MP burn + optional self-heal conversion —
            # Thiên Diệp Già Thiên Chưởng style. ``mp_burn_pct_of_dmg`` reads
            # the cast's dealt damage and burns that fraction off target.mp;
            # ``mp_burn_self_heal_ratio`` (0.0–1.0) routes a fraction of the
            # MP actually drained back to the actor as HP healing via the
            # standard ``_apply_heal`` path (so bleed-heal-reduce / heal-can-
            # crit still apply uniformly). Skipped on evade or zero damage.
            _mp_burn_pct = float(skill_data.get("mp_burn_pct_of_dmg", 0.0))
            if _mp_burn_pct > 0 and not result.is_evaded and dmg > 0:
                burn_amount = max(1, int(dmg * _mp_burn_pct))
                before_mp = target.mp
                target.mp = max(0, target.mp - burn_amount)
                burned = before_mp - target.mp
                if burned > 0:
                    session.log.append(
                        f"    💜 **{actor.name}** thiêu linh — **{target.name}** "
                        f"-{burned:,} MP ({target.mp:,}/{target.mp_max:,})"
                    )
                    _heal_ratio = float(skill_data.get("mp_burn_self_heal_ratio", 0.0))
                    if _heal_ratio > 0:
                        requested = max(1, int(burned * _heal_ratio))
                        healed = session._apply_heal(actor, requested)
                        if healed > 0:
                            session.log.append(
                                f"    🍃 **{actor.name}** Già Thiên Hấp Linh "
                                f"+{healed:,} HP ({int(_heal_ratio * 100)}% MP thiêu)"
                            )

        # Apply skill debuff/CC effects to target
        apply_skill_effects(
            session, skill_data, actor, target, hit=not result.is_evaded,
            mastery_mult=mastery_mult,
        )

        # Trào Tịch Tích Lãng (Thủy B2) — tide-reservoir STORE side. Mirrors
        # the overheal-reservoir capture: bank ``store_pct_of_dmg_dealt`` of
        # the cast's full damage into ``actor.thuy_tide`` (clamped to a
        # matk-scaled cap) and bump the cast counter once per top-level cast.
        # The discharge (every Nth cast) fires later via the ``tide_charge``
        # post-cast consumer in ``cast_consumers.py``. Top-level casts only so
        # multi-hit follow-ups don't double-count a single logical cast.
        _tide = skill_data.get("tide_charge")
        if _tide and not _suppress_extras:
            store_pct = float(_tide.get("store_pct_of_dmg_dealt", 0.0))
            cap_scale = float(_tide.get("reservoir_cap_matk_scale", 0.0))
            cap = int(cap_scale * actor.matk)
            if store_pct > 0 and cap > 0 and dealt_total > 0:
                actor.thuy_tide = min(
                    cap, actor.thuy_tide + int(dealt_total * store_pct)
                )
            actor.thuy_tide_casts += 1

    elif skill_data.get("debuff_only"):
        # 0-damage debuff aura — bypasses the damage roll but still stamps
        # the skill's ``effects`` list on the target. Used by passive
        # formation auras (e.g. Phong Đô Ma Trận) that only exist to
        # debuff what they encircle.
        session.log.append(f"  🪬 **{actor.name}** dùng *{skill_data['vi']}*")
        # Snapshot the prison stack BEFORE the stamp lands so the post-cast
        # tick hook can detect milestone crossings (5/10/15…) and decide
        # whether to fire Vạn Kiếp Phán (the 10-stack capstone consume).
        _vk_prev_stacks = int(getattr(target, "loi_kiep_an_stacks", 0))
        apply_skill_effects(
            session, skill_data, actor, target, hit=True,
            mastery_mult=mastery_mult,
        )
        _vk_spec = skill_data.get("loi_kiep_an_spec")
        if _vk_spec:
            from .formation_prison import process_loi_kiep_an_tick
            process_loi_kiep_an_tick(
                session, actor, target, _vk_spec, _vk_prev_stacks,
            )
        # Thiên Lôi Tru Tà Trận — judgment-by-debuff-count formation. Counts
        # the target's active debuffs (not stacks), refreshes Thiên Lôi Ấn
        # with a dmg_taken_bonus_loi magnitude proportional to that count,
        # then fires Thiên Lôi Phán (regular) or Thiên Lôi Đại Phán (when
        # corruption ≥ threshold AND capstone cooldown clear).
        _tt_spec = skill_data.get("thien_loi_tru_ta_spec")
        if _tt_spec:
            from .formation_prison import process_thien_loi_tru_ta_tick
            process_thien_loi_tru_ta_tick(session, actor, target, _tt_spec)
        # Thái Cực Âm Dương Lôi Đại Trận — phase-alternation capstone. Each
        # tick fires either the Dương pole (attack) or Âm pole (utility);
        # when both poles cap their Khí counter and the fusion CD is clear,
        # next tick emits Thái Cực Lưỡng Nghi Lôi (heavy nuke + uncleansable
        # Tê Liệt + Sốc Điện consume). 10-gem dual_phase_fire bypasses the
        # rotation so both poles fire every tick.
        _ad_spec = skill_data.get("thai_cuc_am_duong_loi_spec")
        if _ad_spec is not None:
            from .formation_prison import process_thai_cuc_tick
            process_thai_cuc_tick(session, actor, target, _ad_spec)
    else:
        # Support / defense skill — applies effects to self (actor)
        session.log.append(f"  🛡️ **{actor.name}** dùng *{skill_data['vi']}*")
        apply_support_skill(
            session, skill_data, actor, target, mastery_mult=mastery_mult,
        )

    # Phù Dao dive resolution — runs once per top-level cast that consumed
    # the buff. Roll the descent rider (linear per-stack chance, capped at
    # 1.0) against the live target, then strip the buff + zero altitude so
    # later casts pay full price.
    if _phu_dao_dive is not None:
        rider_key = _phu_dao_dive.get("rider_key")
        rider_chance = min(1.0, max(0.0, float(_phu_dao_dive.get("rider_chance", 0.0))))
        if rider_key and rider_chance > 0 and target.is_alive():
            rider_meta = EFFECTS.get(rider_key)
            if rider_meta and session.rng.random() < rider_chance:
                inflict_debuff(session, rider_key, rider_meta, target, actor=actor)
        actor.effects.pop("BuffPhuDao", None)
        actor.effect_overrides.pop("BuffPhuDao", None)
        actor.phu_dao_altitude = 0

    if _suppress_extras:
        # Chained / multi-hit follow-ups skip cooldown + per-cast hooks so
        # they don't double-trigger charge / chain / summon / cooldown.
        return

    # ``reduce_self_cooldowns_by`` — Phong Thần Thối-style tempo refresh.
    # Decrements every active entry in ``actor.cooldowns`` by N turns,
    # popping entries that hit 0. Fires BEFORE ``set_cooldown`` below so the
    # skill's own cd isn't refunded by itself. Top-level casts only (the
    # ``_suppress_extras`` early-return above already gates out replays).
    _cd_reduce_n = int(skill_data.get("reduce_self_cooldowns_by", 0))
    if _cd_reduce_n > 0:
        _reduced_keys: list[str] = []
        for _k in list(actor.cooldowns):
            _before = actor.cooldowns[_k]
            if _before <= 0:
                continue
            actor.cooldowns[_k] = max(0, _before - _cd_reduce_n)
            if actor.cooldowns[_k] < _before:
                _reduced_keys.append(_k)
            if actor.cooldowns[_k] == 0:
                actor.cooldowns.pop(_k, None)
        if _reduced_keys:
            session.log.append(
                f"    🍃 **{actor.name}** thoái thuận gió — "
                f"giảm {_cd_reduce_n}t hồi chiêu cho {len(_reduced_keys)} kỹ năng"
            )

    # Hurricane formation counters — Cuồng Phong Đại Trận's twin payoffs.
    # Combo (resettable): non-Phong top-level casts wipe the chain.
    # Cumulative (never resets in-fight): fires the formation's burst every 5
    # Phong casts while the holder carries ``BuffCuongPhongDaiTran``.
    # Auto-fired formation skills don't count — would self-amp infinitely.
    _combo_cat = skill_data.get("category")
    _combo_elem = skill_data.get("element")
    if _combo_cat != "formation":
        if _combo_elem == "phong":
            actor.consecutive_phong_casts = min(actor.consecutive_phong_casts + 1, 5)
            actor.phong_casts_total += 1
            if (
                actor.phong_casts_total % 5 == 0
                and actor.has_effect("BuffCuongPhongDaiTran")
                and target.is_alive()
            ):
                burst_data = registry.get_skill("SkillFrmCuongPhongBurst")
                if burst_data:
                    session.log.append(
                        f"  🌪️ **{actor.name}** **Cuồng Phong Đại Trận** "
                        f"tích đủ 5 đòn — bùng nổ!"
                    )
                    cast_skill(
                        session, actor, target,
                        "SkillFrmCuongPhongBurst", burst_data, 0,
                        _suppress_extras=True,
                    )
        else:
            actor.consecutive_phong_casts = 0
        # Chưởng Tâm Lôi resonator + Tụ Lôi Quyết — parallel to the Phong
        # combo chain. Cap 10, resets on any non-Loi cast. ``BuffTamLoiCong``
        # reads this counter via ``stat:consecutive_loi_casts`` to scale
        # crit_rating + dmg_bonus_loi per stack (clamped at 5-stack via
        # ``max_output``). ``BuffTuLoiQuyet`` (Tụ Lôi Quyết passive) also
        # reads it for matk + dmg_bonus_loi per stack across the full 10
        # cap. No burst hook (unlike Cuồng Phong).
        if _combo_elem == "loi":
            actor.consecutive_loi_casts = min(actor.consecutive_loi_casts + 1, 10)
        else:
            actor.consecutive_loi_casts = 0

    actor.set_cooldown(skill_key, skill_data.get("cooldown", 1))
    # Phi Thiên Lăng Vân — L9 unevadable cadence counter. Only top-level
    # casts count (``_suppress_extras`` already short-circuits above). The
    # cast that CONSUMED the unevadable arm resets the counter to 0 and
    # doesn't count itself, so the next unevadable lands exactly
    # ``interval`` casts later (3 → 4th, 8th, 12th, …). Every other cast
    # increments; the ``interval``-th arms the NEXT cast.
    if actor.phong_unevadable_interval > 0:
        if _phong_bypass_this_cast:
            actor.phong_skill_cast_counter = 0
        elif actor.tick_cadence(
            "phong_skill_cast_counter", actor.phong_unevadable_interval
        ):
            actor.phong_unevadable_armed = True
            session.log.append(
                f"    💨 **{actor.name}** Thiên Phong Vô Ảnh — "
                f"chiêu kế tiếp vô phương né!"
            )
    # Per-fight ``usage_limit`` counter — only top-level casts bump the
    # counter (multi-hit follow-ups already short-circuit above). Filtering
    # against the limit lives in ``CombatSession._choose_skill``.
    actor.skill_usage_count[skill_key] = actor.skill_usage_count.get(skill_key, 0) + 1

    # ── Skill-extras hooks (opt-in via JSON fields) ──────────────────────
    # 1. Multi-hit: replay damage path ``hit_count - 1`` more times. Each
    #    follow-up rolls its own crit/evade and fires on-hit procs but pays
    #    no MP and won't double-tick charge / chain / summon hooks.
    #    ``_hit_index`` + ``_chain_landed`` thread through so per-hit specs
    #    pick the right slot of ``per_hit_specs`` and the terminal finisher
    #    can read each hit's landed status.
    hit_count = max(1, int(skill_data.get("hit_count", 1)))
    # Cửu Âm Hàn Khí (Bắc Minh Băng Phách L1) — DebuffReduceHitCount on the
    # caster shaves one strike off their multi-hit attacks (floored at 1).
    if hit_count > 1 and actor.has_effect("DebuffReduceHitCount"):
        hit_count = max(1, hit_count - 1)
    # Thái Bạch Túy Tiên (L3) — one-shot "+N hit-count on the next cast". Seeded
    # at battle start by the effect-stamp seam; only a top-level cast consumes it
    # (``_suppress_extras`` gates out multi-hit replays / reactive casts so the
    # bonus rides exactly one logical cast), then it's zeroed so it never re-arms.
    if not _suppress_extras and actor.next_skill_hit_count_bonus > 0:
        hit_count += actor.next_skill_hit_count_bonus
        actor.next_skill_hit_count_bonus = 0
    for i in range(hit_count - 1):
        if not target.is_alive():
            break
        session.log.append(f"  🔁 **{actor.name}** liên kích bồi thêm:")
        cast_skill(
            session, actor, target, skill_key, skill_data,
            mp_cost=0, _suppress_extras=True,
            _hit_index=i + 1, _chain_landed=_chain_landed,
        )

    # 1b. Chain finisher — fires once after the full hit chain when every
    # hit landed. Driven by ``chain_finisher`` on skill_data (declared once
    # at the top level, never overridden by per_hit_specs). Currently does
    # ATK-scaled true damage (ignore all defense / resistances). Used by
    # Lục Thần Thương's Thần Diệt 6th-strike payoff.
    finisher = skill_data.get("chain_finisher")
    if (
        finisher
        and _chain_landed is not None
        and len(_chain_landed) >= hit_count
        and all(_chain_landed)
        and target.is_alive()
    ):
        true_pct = float(finisher.get("true_dmg_atk_pct", 0.0))
        if true_pct > 0:
            bonus = max(1, int(actor.atk * true_pct))
            target.take_damage(bonus, bypass_shield=True)
            session.log.append(
                f"    ⚡ **{actor.name}** *{finisher.get('label', 'Thần Diệt')}* "
                f"— Sát Thương Chuẩn -{bonus:,} HP "
                f"| {target.name}: {target.hp:,}/{target.hp_max:,} HP"
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

    # 6+7. Post-cast consumer registry — every finisher hook that strips
    #      target effects after the cast lives in ``cast_consumers.py`` as
    #      a ``@register_post_cast_consumer`` function. Built-ins covered
    #      today: ``consume_target_debuffs`` (Cửu U Thần Trảo) and
    #      ``strip_target_buffs`` (Thánh Quang Phán Quyết). Adding a new
    #      strip-on-cast hook is one decorated function — no edits here.
    apply_post_cast_consumers(skill_data, actor, target, session)


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
            # ``last_hit_only: true`` overrides the per-hit echo count to
            # 1 — formation fires once after the whole main cast completes,
            # regardless of the main skill's hit_count. Designed for heavy
            # eva-scaling formations (Thiên Phong Đại Trận) where firing
            # per-hit would scale damage too aggressively. Formation_echo
            # _bonus from gems still adds on top so 10-gem can grant +1
            # extra echo even in last-hit-only mode.
            base_echoes = 1 if skill_data.get("last_hit_only") else main_hits
            echoes = base_echoes + max(0, int(getattr(actor, "formation_echo_bonus", 0)))
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
    session: "CombatSession", skill_data: dict, actor: Combatant, target: Combatant,
    mastery_mult: float = 1.0,
) -> None:
    """Handle a support/defense skill: instant heals and buff application.

    Instant heal/MP magnitudes come from ``meta.instant_heal_pct`` /
    ``meta.instant_mp_pct`` (with per-cast override via
    ``effect_overrides[<key>]``). Apply chance falls back to
    ``meta.apply_chance`` when the skill JSON omits ``effect_chances[<key>]``.

    ``mastery_mult`` (Skill Mastery, default 1.0) scales the instant
    heal/MP pulse and is stamped onto every buff/debuff override so the
    stat_bonus / dot read sites pick it up. Default 1.0 makes this inert.
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
        # Skill Mastery scales the pulse by ``mastery_mult`` (local float;
        # default 1.0 → identical). This is the core base_dmg=0 heal path.
        heal_pct = float(override.get("instant_heal_pct", meta.instant_heal_pct)) * mastery_mult
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
        mp_pct = float(override.get("instant_mp_pct", meta.instant_mp_pct)) * mastery_mult
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
            # Stamp ``_mastery_mult`` copy-on-write onto the buff override (ALWAYS,
            # default 1.0). Read sites (get_combat_modifiers / defense_aegis)
            # multiply by it; stamping 1.0 even on non-mastered casts prevents a
            # stale elevated mult surviving a later re-apply.
            stamp = {
                k: v for k, v in override.items() if k != "duration"
            }
            stamp["_mastery_mult"] = mastery_mult
            actor.apply_effect(effect_key, dur, overrides=stamp)
            session.log.append(
                f"    {meta.emoji} **{meta.vi}** ({dur}t) — {meta.description_vi}"
            )
            # Defense-aegis shield grant hook — fires only if the buff declares
            # ``_aegis.shield_grant`` (no-op otherwise). See ``defense_aegis``.
            from .defense_aegis import grant_shield_on_cast
            grant_shield_on_cast(session, actor, effect_key)

        elif meta.kind.value in ("debuff", "cc"):
            # CC skills with base_dmg=0 that debuff the target (e.g. CCBind skill)
            base_chance = effect_chances.get(effect_key, meta.apply_chance)
            effective_chance = _effective_debuff_chance(base_chance, actor, target)
            if effective_chance >= 1.0 or session.rng.random() < effective_chance:
                # Stamp ``_mastery_mult`` copy-on-write (ALWAYS, default 1.0) —
                # never mutate ``skill_data["effect_overrides"][...]``.
                stamped = {**override, "_mastery_mult": mastery_mult}
                inflict_debuff(
                    session, effect_key, meta, target, actor=actor,
                    overrides=stamped,
                )


def apply_skill_effects(
    session: "CombatSession", skill_data: dict,
    actor: Combatant, target: Combatant, hit: bool,
    mastery_mult: float = 1.0,
) -> None:
    """Apply all effect_keys from a skill's effects list to the appropriate target.

    For debuffs/CC: checks the skill's ``effect_chances`` dict, falling back
    to ``meta.apply_chance`` when the skill doesn't specify a per-effect
    chance. The result is multiplied by ``(1 - target.debuff_immune_pct)``
    to get the effective proc probability.

    Special keywords handled in-line (not in the EFFECTS registry).

    ``mastery_mult`` (Skill Mastery, default 1.0) is stamped copy-on-write
    onto every buff/debuff override so the stat_bonus / dot read sites scale
    by it. Default 1.0 keeps this inert.
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
            # Stamp ``_mastery_mult`` copy-on-write (ALWAYS, default 1.0) onto the
            # buff override so the stat_bonus / aegis read sites scale by it.
            stamp = dict(stamp or {})
            stamp["_mastery_mult"] = mastery_mult
            actor.apply_effect(effect_key, dur, overrides=stamp)
            session.log.append(f"    {meta.emoji} **{actor.name}** nhận **{meta.vi}** ({dur}t)")
            # Defense-aegis shield grant hook — fires only if the buff declares
            # ``_aegis.shield_grant`` (no-op otherwise). See ``defense_aegis``.
            from .defense_aegis import grant_shield_on_cast
            grant_shield_on_cast(session, actor, effect_key)
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
                # Stamp ``_mastery_mult`` copy-on-write (ALWAYS, default 1.0) —
                # never mutate ``skill_data["effect_overrides"][...]``.
                stamped = {**(effective_override or {}), "_mastery_mult": mastery_mult}
                inflict_debuff(
                    session, effect_key, meta, target, actor=actor,
                    overrides=stamped,
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
    # Pre-stamp interceptor registry — every guard that can abort the
    # stamp (immunity flags, Vô Tướng Phong charge absorber, hard-CC
    # immunity, effect_resist RNG roll, Tuyệt Diệu Vô Ảnh slow immunity)
    # lives in ``inflict_interceptors.py`` as a decorated function.
    # First handler to return True aborts; handlers own their own log
    # appends and side-effects (charge decrement, buff strip, etc.).
    # Adding a new immunity/resist gate is one decorated function — no
    # edits here.
    if dispatch_pre_stamp_interceptors(
        session, effect_key, meta, target, actor, overrides,
    ):
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

    # Phù Dao CC reset — a holder of ``BuffPhuDao`` who eats a lockout CC
    # (skips_turn / prevents_skills) is knocked off the whirlwind and loses
    # the accumulated altitude. The buff itself stays so they can re-climb
    # next turn if it still has duration. Opt-in via the buff's
    # ``reset_on_cc`` override (default off) so other buffs that might one
    # day reuse altitude-style mechanics can choose differently.
    if (
        target.phu_dao_altitude > 0
        and (meta.skips_turn or meta.prevents_skills)
        and target.has_effect("BuffPhuDao")
    ):
        _pd_ovr = target.effect_overrides.get("BuffPhuDao", {}) or {}
        if _pd_ovr.get("reset_on_cc", False):
            target.phu_dao_altitude = 0
            session.log.append(
                f"    🪁 **{target.name}** bị **{meta.vi}** — rơi mất Cao Độ."
            )

    # Truy Kích Liên Vũ (Phong B1) — CC-taken breaks the self-combo. The combo
    # skill declares ``break_on_cc_taken``; any hard CC (skips_turn /
    # prevents_skills, same gate as the Phù Dao reset above) snaps the chain
    # to 0. Gated on the holder actually owning a combo skill so a generic
    # ``phong_combo`` field can't be reset by a CC on an actor who never built
    # the chain — and inert for enemies (empty skill_keys / no combo skill).
    if (
        target.phong_combo > 0
        and (meta.skips_turn or meta.prevents_skills)
    ):
        for _sk in target.skill_keys:
            _cd = registry.get_skill(_sk)
            _spec = (_cd or {}).get("combo_counter_scaling")
            if _spec and _spec.get("break_on_cc_taken"):
                target.phong_combo = 0
                session.log.append(
                    f"    🌀 **{target.name}** bị **{meta.vi}** — đứt chuỗi Liên Vũ."
                )
                break

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

    # Stack-stamp registry dispatch — every per-effect ``add_stack +
    # log`` branch lives in ``stack_stampers.py``. Returns True if a
    # stamper handled the effect (caller exits); False falls through to
    # the generic DoT-propagate-and-log tail below for non-stacking
    # debuffs/CC. Adding a new stacker is one ``register_simple_stamp``
    # call (or ``@register_stack_stamper`` for specials) — no edits here.
    if dispatch_stack_stamp(session, effect_key, actor, target, meta, dur, overrides):
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
    # A basic attack is non-Phong / non-Loi by definition — breaks any active
    # Hurricane (Cuồng Phong) or Chưởng Tâm Lôi resonator combo chain. Mirrors
    # the reset path in ``cast_skill`` so a silenced / OOM holder loses the
    # chain the same way as a cross-element cast.
    actor.consecutive_phong_casts = 0
    actor.consecutive_loi_casts = 0
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
