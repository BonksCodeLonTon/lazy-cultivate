"""Liệt Diễm Phần Thiên — Hỏa Thần avatar cadence (PRE_TURN).

L9 Hỏa Thần Hóa Thân — every ``lietdiem_avatar_interval`` acted turns,
self-apply ``BuffHoaThanHoaThan`` for ``lietdiem_avatar_duration`` turns
(crit + capped phys-reduce + small final-dmg burst). dot_can_crit is a
permanent L9 stat granted in the milestone, not handled here.

This rides PRE_TURN — not PERIODIC — so:

  (a) the 2-turn avatar is stamped *before* the holder acts and therefore
      covers two full actionable turns. A PERIODIC stamp would be applied at
      end-of-round and then immediately decremented by the expiry hook
      (priority 80) in the *same* sweep, costing one of the two turns.
  (b) the counter only advances on acted turns — ``_take_turn`` dispatches
      PRE_TURN after the CC-skip/phase-lock/dodge returns — so a stunned
      turn never bumps it, matching the "every N acted turns" intent and the
      saint-realm cadence in ``hoang_co.py``.

The body also opens combat already in avatar form (the L9 milestone stamps
the buff at build time); this hook drives the recurring rotation thereafter.

Inert for every other build: the gate is the per-body avatar flag + non-zero
interval, both 0/False on every non-Liệt-Diễm combatant.
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS, default_duration

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook

_AVATAR_BUFF = "BuffHoaThanHoaThan"


@register_hook(phase=TurnPhase.PRE_TURN, name="lietdiem_avatar_cadence", priority=39)
def _lietdiem_avatar(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.lietdiem_avatar_enabled:
        return
    if not actor.tick_cadence("lietdiem_avatar_counter", actor.lietdiem_avatar_interval):
        return
    meta = EFFECTS.get(_AVATAR_BUFF)
    if meta is None:
        return
    dur = actor.lietdiem_avatar_duration or default_duration(_AVATAR_BUFF)
    actor.apply_effect(_AVATAR_BUFF, dur)
    ctx.log.append(
        f"  🔥 **{actor.name}** **Hỏa Thần Hóa Thân** — "
        f"hóa thân Hỏa Thần {dur} lượt!"
    )
