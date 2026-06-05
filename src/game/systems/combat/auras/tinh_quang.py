"""Tịnh Quang Hộ Pháp — Tịnh Quang Tẩy Trần self-cleanse (PRE_TURN).

L3 mechanic. Every ``quang_self_cleanse_interval`` acted turns, remove up to
``quang_self_cleanse_count`` cleansable debuffs from the holder itself. Runs in
PRE_TURN (after the CC-skip / dodge returns in ``_take_turn``), so a turn lost to
a stun never advances the cadence — it tracks *acted* turns only (mirroring the
huyen_am / hoang_co cadence auras). Reuses the ``EffectMeta.cleansable`` filter
(same one the saint L5 cleanse uses).

Inert for every other build: the gate is the L3 ``quang_self_cleanse_interval``,
which no other combatant carries.
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="tinh_quang_self_cleanse", priority=12)
def _self_cleanse(ctx: TurnContext) -> None:
    actor = ctx.actor
    interval = actor.quang_self_cleanse_interval
    if interval <= 0:
        return
    actor.quang_cleanse_turn_counter += 1
    if actor.quang_cleanse_turn_counter % interval != 0:
        return

    count = max(1, actor.quang_self_cleanse_count)
    cleansable = [
        k for k in list(actor.effects)
        if (m := EFFECTS.get(k)) is not None and m.cleansable
    ]
    removed = 0
    for k in cleansable[:count]:
        del actor.effects[k]
        actor.effect_overrides.pop(k, None)
        removed += 1
    if removed:
        ctx.log.append(
            f"  ✨ **{actor.name}** Tịnh Quang Tẩy Trần — thanh tẩy {removed} "
            f"hiệu ứng bất lợi"
        )
