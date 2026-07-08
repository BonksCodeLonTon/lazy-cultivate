"""Quang Minh Thánh Thể — radiance aura + Great Purification (PRE_TURN).

Two mechanics ride one hook:

L1 Quang Minh Chi Vực — every acted turn: ``qm_aura_blind_chance`` to inflict
Lóa Mắt on the opponent (through ``inflict_debuff`` so debuff-shrug applies)
and refresh DebuffQuangMinhVuc (crit_rating −60). A LANDED aura blind banks
+1 Thánh Quang stack (cap ``qm_stack_cap``) — BuffThanhQuangTichTu's scaling
rules turn the counter into live dmg_bonus_quang + strip-chance.

L9 Đại Tịnh Hóa Thuật — every ``qm_purify_interval`` acted turns (one turn
sooner while Thánh Quang is at ``qm_purify_fast_stack_gate``): cleanse ALL of
the holder's cleansable debuffs, strip up to ``qm_purify_strip_count`` random
enemy buffs (same kind-filter as the #11 judgment strip), heal
``qm_purify_heal_pct`` of max HP (through ``_apply_heal`` — anti-heal
counters apply) and don BuffThanhKhiet (75% debuff-shrug, 2 turns).

Mirrors the tick_cadence PRE_TURN pattern — acted turns only. Inert for every
other build (both gates default 0).
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS, EffectKind

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="quang_minh_radiance", priority=33)
def _quang_minh_radiance(ctx: TurnContext) -> None:
    actor = ctx.actor
    target = ctx.target
    session = ctx.session

    # ── L1 radiance aura ─────────────────────────────────────────────────────
    if actor.qm_aura_blind_chance > 0 and target.is_alive():
        from ..casting import inflict_debuff
        _vuc = EFFECTS.get("DebuffQuangMinhVuc")
        if _vuc is not None:
            inflict_debuff(session, "DebuffQuangMinhVuc", _vuc, target, actor=actor)
        if session.rng.random() < actor.qm_aura_blind_chance:
            _lm = EFFECTS.get("DebuffLoaMat")
            if _lm is not None:
                inflict_debuff(session, "DebuffLoaMat", _lm, target, actor=actor)
            # Bank a Thánh Quang stack only when the blind is actually ON the
            # target after the inflict (fresh stamp or refresh both count —
            # a shrugged/immune inflict on a clean target banks nothing).
            if target.has_effect("DebuffLoaMat") \
                    and actor.qm_stack_cap > 0 \
                    and actor.qm_thanh_quang_stacks < actor.qm_stack_cap:
                actor.qm_thanh_quang_stacks += 1
                ctx.log.append(
                    f"  ✨ **{actor.name}** Thánh Quang Tích Tụ "
                    f"[×{actor.qm_thanh_quang_stacks}/{actor.qm_stack_cap}]"
                )

    # ── L9 Great Purification ────────────────────────────────────────────────
    if actor.qm_purify_interval <= 0:
        return
    interval = actor.qm_purify_interval
    if actor.qm_purify_fast_stack_gate > 0 \
            and actor.qm_thanh_quang_stacks >= actor.qm_purify_fast_stack_gate:
        interval = max(1, interval - 1)
    if not actor.tick_cadence("qm_purify_turn_counter", interval):
        return
    # 1. Cleanse ALL own cleansable debuffs.
    cleansed = 0
    for k in [k for k in list(actor.effects)
              if (m := EFFECTS.get(k)) is not None and m.cleansable]:
        del actor.effects[k]
        actor.effect_overrides.pop(k, None)
        cleansed += 1
    # 2. Strip up to N random enemy buffs (kind-filter mirrors #11's judgment).
    stripped = 0
    if target.is_alive() and actor.qm_purify_strip_count > 0:
        _buffs = [
            k for k in list(target.effects)
            if (m := EFFECTS.get(k)) is not None and m.kind is EffectKind.BUFF
        ]
        session.rng.shuffle(_buffs)
        for k in _buffs[: actor.qm_purify_strip_count]:
            target.effects.pop(k, None)
            target.effect_overrides.pop(k, None)
            stripped += 1
    # 3. Heal through the central pipeline (anti-heal counters apply).
    healed = 0
    if actor.qm_purify_heal_pct > 0:
        healed = session._apply_heal(
            actor, int(actor.hp_max * actor.qm_purify_heal_pct)
        )
    # 4. Don the ward (a buff — stamped after the cleanse, untouched by it).
    actor.apply_effect("BuffThanhKhiet", 2)
    ctx.log.append(
        f"  🕊️ **{actor.name}** ĐẠI TỊNH HÓA THUẬT — tẩy {cleansed} uế, "
        f"tước {stripped} phúc, hồi {healed:,} HP, khoác Thanh Khiết!"
    )
