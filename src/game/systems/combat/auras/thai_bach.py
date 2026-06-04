"""Thái Bạch Canh Kim — periodic guaranteed-crit cadence (PRE_TURN).

Huyết Lạp Thái Bạch (L9) arms a guaranteed crit every Nth acted turn. This
hook runs in the PRE_TURN phase, which ``_take_turn`` dispatches AFTER the
CC-skip / phase-lock / dodge returns — so a turn the actor loses to a stun
never bumps the counter, and the cadence tracks *acted* turns only (the spec's
intent). The arm is consumed on the first landed damaging hit (casting.py).

Inert for every other build: the gate is the L9 marker buff + a non-zero
interval, neither of which any other combatant carries.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="thai_bach_periodic_crit", priority=40)
def _arm_periodic_crit(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.has_effect("BuffHuyetLapThaiBach"):
        return
    interval = actor.bleed_hunter_periodic_interval
    if interval <= 0:
        return
    actor.bleed_hunter_turn_counter += 1
    if actor.bleed_hunter_turn_counter % interval == 0:
        actor.bleed_hunter_crit_armed = True
        ctx.log.append(
            f"  🩸 **{actor.name}** Huyết Lạp Thái Bạch — đòn kế tiếp chắc chắn Bạo Kích!"
        )
