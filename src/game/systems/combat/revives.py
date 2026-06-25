"""On-revive cascade — phoenix → buff → Chân Mệnh Lôi Phù.

Three revive sources are tried in priority order whenever a combatant
would die. Only the FIRST eligible one fires; the rest see
``ctx.scratch["revived"] = True`` and skip via their predicate.

Priority order matches the cascade that lived inline at the bottom of
``_try_phoenix_revive`` in ``session.py``:

  10  phoenix      — Niết Bàn Trùng Sinh (once-per-combat, restores %hp_max,
                     bumps stats, purges DoTs)
  20  buff         — Thiên Sứ Hộ Mệnh class (consumes one ``revive_hp_pct``
                     buff and revives at that fraction)
  30  chan_menh    — Chân Mệnh Lôi Phù (Lôi passive: restores hp, resets
                     Lôi cooldowns, fires Lôi Phù Phản Phán nuke)

The caller (``CombatSession._try_revive``) checks ``ctx.actor.is_alive()``
after ``run_phase(ON_REVIVE, ctx)`` to decide whether the death is final.
``ctx.actor`` is the dying combatant; ``ctx.target`` is their opponent
(used by Chân Mệnh's counter-nuke).
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.constants.balance import MAX_FINAL_DMG_REDUCE
from src.game.constants.effects import EffectKey
from src.game.engine.damage.color import colorize_damage
from src.game.engine.effects import (
    EFFECTS,
    EffectKind,
    default_duration,
    get_combat_modifiers,
)

from .context import TurnContext
from .hooks import TurnPhase, register_hook


def _not_yet_revived(ctx: TurnContext) -> bool:
    """Hook predicate — only run if the dying combatant is still down."""
    return not ctx.scratch.get("revived") and not ctx.actor.is_alive()


def _phoenix_rebirth_ready(ctx: TurnContext) -> bool:
    """Predicate for the L9 Phượng Hoàng Trọng Sinh 3-charge revive.

    Gated tighter than ``_not_yet_revived`` so the hook only claims the death
    for the upgraded Hỏa body that still has rebirth charges. When it fires it
    sets ``ctx.scratch["revived"]`` like the other revive hooks, so the
    lower-priority generic seams (phoenix / buff / chân mệnh) skip — at L9 the
    body's own ``BuffChanDuongNietBan`` revive never triggers because this hook
    claims the death first.
    """
    return (
        _not_yet_revived(ctx)
        and ctx.actor.hoa_revive_upgraded
        and ctx.actor.hoa_revive_charges > 0
    )


@register_hook(
    phase=TurnPhase.ON_REVIVE, name="phuong_hoang_trong_sinh", priority=5,
    predicate=_phoenix_rebirth_ready,
)
def _phuong_hoang_trong_sinh(ctx: TurnContext) -> bool | None:
    """Chân Dương Bất Diệt Thể L9 — Tam Sinh Bất Diệt 3-charge phoenix rebirth.

    Fires at priority 5 (BEFORE phoenix=10 / buff=20 / chân mệnh=30) so it claims
    the death first; the predicate already confirmed the body + a remaining
    charge. Each rebirth: spend one charge, restore ``hoa_revive_hp_pct_l9`` of
    hp_max, stamp ``BuffChanDuongDuHoa`` (+40% final dmg, 2 turns) BEFORE the
    burst so the burst rides the buff, then drop Phượng Hoàng Lửa (300% atk +
    300% matk) on the opponent. No recursion: this fires from ON_REVIVE, not a
    per-hit sweep, and the burst is ``_suppress_extras=True``.
    """
    combatant = ctx.actor
    combatant.hoa_revive_charges -= 1
    revived_hp = max(1, int(combatant.hp_max * combatant.hoa_revive_hp_pct_l9))
    combatant.hp = revived_hp

    # The upgraded body still carries the L3 ``BuffChanDuongNietBan`` (effects
    # accumulate across milestones), which would otherwise grant a 4th, weaker
    # revive once the 3 phoenix charges are spent. Strip it so the L9 rebirth
    # count is EXACTLY the charge total — matching the design ("3×/combat at
    # L9; the generic buff seam never fires"). Idempotent pop.
    combatant.effects.pop("BuffChanDuongNietBan", None)
    combatant.effect_overrides.pop("BuffChanDuongNietBan", None)

    # +40% post-revive window — applied BEFORE the burst so the burst is amped.
    du_hoa_meta = EFFECTS.get("BuffChanDuongDuHoa")
    du_hoa_dur = 2
    if du_hoa_meta is not None:
        combatant.apply_effect("BuffChanDuongDuHoa", du_hoa_dur)

    ctx.log.append(
        f"  🦅🔥 **{combatant.name}** **PHƯỢNG HOÀNG TRỌNG SINH!** "
        f"Hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP "
        f"(còn {combatant.hoa_revive_charges} lần) · Dư Hỏa +40% ST ({du_hoa_dur}t)"
    )

    opponent = ctx.target
    if opponent.is_alive():
        burst_data = registry.get_skill("SkillHoaPhuongHoangBurst")
        if burst_data is not None:
            ctx.log.append(
                f"  🦅 **{combatant.name}** giáng **Phượng Hoàng Lửa** "
                f"xuống **{opponent.name}**!"
            )
            # Lazy import to avoid the casting → revives import cycle (mirrors
            # the chân mệnh hook's ``from .casting import inflict_debuff``).
            from .casting import cast_skill
            cast_skill(
                ctx.session, combatant, opponent,
                "SkillHoaPhuongHoangBurst", burst_data, 0,
                _suppress_extras=True,
            )

    ctx.scratch["revived"] = True
    return True


def _spring_flowing(actor) -> bool:
    """True while the Mộc body's healing is NOT choked off.

    The Undying Spring only holds while the spring still flows — i.e. the body
    has positive HP regen and no anti-heal pressure has pushed its
    ``heal_taken_reduce`` up to the gate. An enemy that lands enough anti-heal
    (Cắt Đứt etc.) chokes the spring and the cheat-death stops firing. Bleed is
    a pure DoT in this game (no heal-reduce), so the single ``heal_taken_reduce``
    aggregate is the whole signal.
    """
    heal_reduce = get_combat_modifiers(actor).get("heal_taken_reduce", 0.0)
    return actor.hp_regen_pct > 0 and heal_reduce < actor.moc_undying_heal_reduce_gate


def _undying_spring_ready(ctx: TurnContext) -> bool:
    """Predicate for the L9 Trường Xuân Bất Tử cheat-death.

    Gated tighter than ``_not_yet_revived`` so the hook only claims the death for
    the Mộc body whose cooldown is ready AND whose spring is still flowing. When
    it fires it sets ``ctx.scratch["revived"]`` like every other revive hook, so
    the lower-priority generic seams skip.
    """
    actor = ctx.actor
    return (
        _not_yet_revived(ctx)
        and actor.moc_undying_spring_enabled
        and actor.moc_undying_cd == 0
        and _spring_flowing(actor)
    )


@register_hook(
    phase=TurnPhase.ON_REVIVE, name="truong_xuan_undying", priority=5,
    predicate=_undying_spring_ready,
)
def _truong_xuan_undying(ctx: TurnContext) -> bool | None:
    """Trường Xuân Linh Mộc L9 — Undying Spring death-defiance.

    Fires at priority 5 (same tier as the Hỏa L9 phoenix; both are predicate-
    gated so only the matching body's hook ever claims a given death). A lethal
    hit leaves the body at ``moc_undying_min_hp`` (1) instead of killing it, then
    locks the spring for ``moc_undying_cooldown_turns`` of the body's own turns
    (decremented by the truong_xuan PRE_TURN aura). No burst, no stat buff — a
    pure tanky cheat-death matching the "Eternal Spring" name.
    """
    combatant = ctx.actor
    combatant.hp = max(1, combatant.moc_undying_min_hp)
    combatant.moc_undying_cd = combatant.moc_undying_cooldown_turns
    ctx.log.append(
        f"  🌿♻️ **{combatant.name}** **TRƯỜNG XUÂN BẤT TỬ!** Xuân khí chưa tàn — "
        f"trụ lại {combatant.hp:,} HP (hồi chiêu {combatant.moc_undying_cd} lượt)"
    )
    ctx.scratch["revived"] = True
    return True


def _niet_ban_rebirth_ready(ctx: TurnContext) -> bool:
    """Predicate for the Niết Bàn Bất Diệt L6 once-per-fight nirvana rebirth.

    Gated tighter than ``_not_yet_revived`` so the hook only claims the death for
    the Niết Bàn body whose single rebirth charge is unused. Sets
    ``ctx.scratch["revived"]`` when it fires so the lower-priority generic seams
    skip (the body has no BuffChanDuongNietBan-style fallback to suppress).
    """
    return (
        _not_yet_revived(ctx)
        and ctx.actor.niet_ban_revive_enabled
        and not ctx.actor.niet_ban_revive_used
    )


@register_hook(
    phase=TurnPhase.ON_REVIVE, name="niet_ban_trong_sinh", priority=5,
    predicate=_niet_ban_rebirth_ready,
)
def _niet_ban_trong_sinh(ctx: TurnContext) -> bool | None:
    """Niết Bàn Bất Diệt Thể L6 — once-per-fight Nirvana Rebirth.

    Fires at priority 5 (before the generic phoenix=10 / buff=20 / chân-mệnh=30
    seams) so it claims the death; the predicate already confirmed the body and an
    unspent charge. Restores ``niet_ban_revive_pct`` of hp_max (+5% per accumulated
    Nghiệp Hỏa tier), then — when ``niet_ban_revive_clear_debuffs`` — wipes every
    debuff for a clean slate (resets the stack-DoT carriers + drops debuff-kind
    effects). When L9 (``niet_ban_post_revive_boost``) is unlocked it stamps
    BuffCuuChuyenNietBan, the 4-turn +50% all-stat berserk window. No burst, no
    recursion (fires from ON_REVIVE, not a per-hit sweep).
    """
    combatant = ctx.actor
    combatant.niet_ban_revive_used = True

    pct = combatant.niet_ban_revive_pct + (
        combatant.nb_nghiep_tier * combatant.nb_nghiep_revive_pct_per_tier
    )
    revived_hp = max(1, int(combatant.hp_max * pct))
    combatant.hp = revived_hp

    cleared = 0
    if combatant.niet_ban_revive_clear_debuffs:
        # Reset the stack-DoT carriers (burn/bleed/shock/poison proxies), then
        # drop every debuff-kind effect — Nirvana burns the karma away.
        combatant.burn_stacks = 0
        combatant.bleed_stacks = 0
        combatant.shock_stacks = 0
        combatant.poison_stacks = 0
        for effect_key in list(combatant.effects):
            meta = EFFECTS.get(effect_key)
            if meta is not None and meta.kind == EffectKind.DEBUFF:
                combatant.effects.pop(effect_key, None)
                combatant.effect_overrides.pop(effect_key, None)
                cleared += 1

    boost_tag = ""
    if combatant.niet_ban_post_revive_boost and EFFECTS.get("BuffCuuChuyenNietBan"):
        dur = default_duration("BuffCuuChuyenNietBan")
        combatant.apply_effect("BuffCuuChuyenNietBan", dur)
        boost_tag = f" · **Cửu Chuyển Niết Bàn** +50% toàn chỉ số ({dur}t)"

    nghiep_tag = (
        f" · Nghiệp Hỏa ×{combatant.nb_nghiep_tier}"
        if combatant.nb_nghiep_tier > 0 else ""
    )
    ctx.log.append(
        f"  🔥♻️ **{combatant.name}** **NIẾT BÀN TRỌNG SINH!** "
        f"Hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP"
        + (f" · xóa {cleared} debuff" if cleared else "")
        + nghiep_tag
        + boost_tag
    )
    ctx.scratch["revived"] = True
    return True


@register_hook(
    phase=TurnPhase.ON_REVIVE, name="phoenix_revive", priority=10,
    predicate=_not_yet_revived,
)
def _phoenix_revive(ctx: TurnContext) -> bool | None:
    combatant = ctx.actor
    if combatant.phoenix_revive_used or combatant.phoenix_revive_pct <= 0:
        return None

    combatant.phoenix_revive_used = True
    revived_hp = max(1, int(combatant.hp_max * combatant.phoenix_revive_pct))
    combatant.hp = revived_hp

    buff = combatant.phoenix_revive_buff_pct
    if buff > 0:
        combatant.atk = int(combatant.atk * (1.0 + buff))
        combatant.matk = int(combatant.matk * (1.0 + buff))
        combatant.def_stat = int(combatant.def_stat * (1.0 + buff))
        combatant.final_dmg_bonus += buff
        combatant.final_dmg_reduce = min(
            MAX_FINAL_DMG_REDUCE, combatant.final_dmg_reduce + buff,
        )

    # Rebirth purges DoTs and adverse stacks.
    combatant.burn_stacks = 0
    combatant.bleed_stacks = 0
    combatant.shock_stacks = 0
    combatant.poison_stacks = 0
    combatant.effects.clear()

    buff_tag = f" · ST/Giáp +{buff * 100:.0f}%" if buff > 0 else ""
    ctx.log.append(
        f"  🔥🦅 **{combatant.name}** **NIẾT BÀN TRÙNG SINH!** "
        f"Hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP{buff_tag}"
    )
    ctx.scratch["revived"] = True
    return True


@register_hook(
    phase=TurnPhase.ON_REVIVE, name="buff_revive", priority=20,
    predicate=_not_yet_revived,
)
def _buff_revive(ctx: TurnContext) -> bool | None:
    combatant = ctx.actor
    for effect_key in list(combatant.effects):
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        override = combatant.effect_overrides.get(effect_key) or {}
        override_sb = override.get("stat_bonus") or {}
        revive_pct = float(override_sb.get(
            "revive_hp_pct",
            meta.stat_bonus.get("revive_hp_pct", 0.0),
        ))
        if revive_pct <= 0:
            continue

        revived_hp = max(1, int(combatant.hp_max * revive_pct))
        combatant.hp = revived_hp
        combatant.effects.pop(effect_key, None)
        combatant.effect_overrides.pop(effect_key, None)
        ctx.log.append(
            f"  {meta.emoji} **{combatant.name}** **{meta.vi}** vỡ tan — "
            f"hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP, "
            f"mất hào quang phù hộ."
        )
        ctx.scratch["revived"] = True
        return True
    return None


@register_hook(
    phase=TurnPhase.ON_REVIVE, name="chan_menh_loi_phu", priority=30,
    predicate=_not_yet_revived,
)
def _chan_menh_loi_phu(ctx: TurnContext) -> bool | None:
    combatant = ctx.actor
    if combatant.chan_menh_loi_phu_used:
        return None
    if "SkillPassiveChanMenhLoiPhu" not in combatant.skill_keys:
        return None

    skill_data = registry.get_skill("SkillPassiveChanMenhLoiPhu") or {}
    revive_pct = float(skill_data.get("revive_hp_pct", 0.15))
    nuke_base = int(skill_data.get("nuke_base_dmg", 1500))
    nuke_matk = float(skill_data.get("nuke_matk_scale", 1.5))
    te_liet_chance = float(skill_data.get("te_liet_chance", 1.0))
    te_liet_duration = int(skill_data.get("te_liet_duration", 1))
    refresh_loi = int(skill_data.get("refresh_loi_stacks", 10))

    combatant.chan_menh_loi_phu_used = True
    revived_hp = max(1, int(combatant.hp_max * revive_pct))
    combatant.hp = revived_hp
    combatant.consecutive_loi_casts = max(
        combatant.consecutive_loi_casts, refresh_loi
    )
    # Reset every Lôi-element skill's cooldown — keeps non-Lôi cooldowns
    # intact (the talisman pertains only to the lightning lineage).
    for sk_key in list(combatant.cooldowns.keys()):
        sk = registry.get_skill(sk_key)
        if sk and sk.get("element") == "loi":
            combatant.cooldowns[sk_key] = 0

    ctx.log.append(
        f"  🌩️⚡ **{combatant.name}** **CHÂN MỆNH LÔI PHÙ KÍCH HOẠT!** "
        f"Hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP · "
        f"Tụ Lôi nạp đầy [×{refresh_loi}] · Lôi pháp khôi phục"
    )

    opponent = ctx.target
    if opponent.is_alive():
        burst = max(1, int(nuke_base + nuke_matk * combatant.matk))
        opponent.take_damage(burst)
        tag = colorize_damage(f"-{burst:,} HP", "loi")
        ctx.log.append(
            f"  ⚡ **{combatant.name}** **Lôi Phù Phản Phán** → "
            f"**{opponent.name}** {tag}"
        )
        if (
            te_liet_chance > 0
            and te_liet_duration > 0
            and opponent.is_alive()
            and (te_liet_chance >= 1.0 or ctx.rng.random() < te_liet_chance)
        ):
            te_meta = EFFECTS.get(EffectKey.DEBUFF_TE_LIET.value)
            if te_meta is not None:
                # Lazy import to avoid casting → revives cycle (casting
                # already imports session indirectly via dmg_riders etc.).
                from .casting import inflict_debuff
                inflict_debuff(
                    ctx.session, EffectKey.DEBUFF_TE_LIET.value, te_meta,
                    opponent, actor=combatant,
                    overrides={"duration": te_liet_duration},
                )
    ctx.scratch["revived"] = True
    return True
