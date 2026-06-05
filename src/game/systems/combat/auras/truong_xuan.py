"""Trường Xuân Linh Mộc — Undying Spring cooldown tick (PRE_TURN).

L9 support. The ON_REVIVE ``truong_xuan_undying`` hook sets ``moc_undying_cd`` to
``moc_undying_cooldown_turns`` whenever the cheat-death fires; this PRE_TURN hook
ticks that counter down by one each turn the body actually acts (PRE_TURN runs
after the CC-skip / dodge returns in ``_take_turn``, so a turn lost to a stun
never advances the cooldown — the cadence tracks *acted* turns only, mirroring
the huyen_am auto-Nhập-Ma pattern). When the counter reaches 0 the Undying
Spring re-arms.

Runs at priority 5 (before the other pre-turn auras). Inert for every other
build: the gate is the L9 ``moc_undying_spring_enabled`` flag, which no other
combatant carries.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="truong_xuan_undying_cd", priority=5)
def _undying_cooldown_tick(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.moc_undying_spring_enabled:
        return
    if actor.moc_undying_cd > 0:
        actor.moc_undying_cd -= 1
