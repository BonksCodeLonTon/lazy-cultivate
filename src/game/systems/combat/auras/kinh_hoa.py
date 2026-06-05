"""Kính Hoa Thủy Nguyệt — debuff-transfer pre-turn aura.

Extracted from ``CombatSession._try_transfer_debuffs``. Once per turn,
the holder rolls the buff's transfer chance; on success one random
non-stack debuff/CC is popped off the actor and re-stamped on the
opponent with the same remaining duration and per-instance override.

Stack-based DoTs (burn/bleed/shock/poison) are excluded because their
actual damage lives on per-stack counters, not the effect entry —
transferring the entry alone would be a no-op. Hard-CC immunity on the
opponent silently skips the transfer for ``skips_turn`` /
``prevents_skills`` effects so a world boss can't be chain-stunned via
mirror.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS, EffectKind, get_combat_modifiers

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="kinh_hoa_thuy_nguyet", priority=10)
def _try_transfer_debuffs(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if not actor.has_effect(EffectKey.BUFF_KINH_HOA_THUY_NGUYET):
        return
    chance = float(get_combat_modifiers(actor).get("debuff_transfer_on_turn_pct", 0.0))
    if chance <= 0:
        return
    candidates = []
    for k in list(actor.effects):
        meta = EFFECTS.get(k)
        if meta is None:
            continue
        if meta.kind not in (EffectKind.DEBUFF, EffectKind.CC):
            continue
        if not meta.cleansable:
            continue
        if meta.stack_kind:
            continue
        candidates.append(k)
    if not candidates:
        return
    if ctx.rng.random() >= chance:
        return
    pick = ctx.rng.choice(candidates)
    meta = EFFECTS[pick]
    if (opponent.immune_hard_cc or opponent.has_effect("BuffHoangCoThanhVuc")) and (meta.skips_turn or meta.prevents_skills):
        return
    if pick == EffectKey.DEBUFF_DOC_TO and opponent.poison_immunity:
        return
    remaining = actor.effects.get(pick, 0)
    override = actor.effect_overrides.get(pick)
    actor.effects.pop(pick, None)
    actor.effect_overrides.pop(pick, None)
    opponent.apply_effect(pick, remaining, overrides=override)
    ctx.log.append(
        f"  🪞 **{actor.name}** Kính Hoa Thủy Nguyệt → đẩy ngược "
        f"**{meta.vi}** ({remaining}t) sang **{opponent.name}**"
    )
