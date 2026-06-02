"""Per-summon damage tick.

One PERIODIC hook (priority 30) — when the actor carries any active
summon (Vạn Kiếm sword spirits, Cửu Long dragons, etc.) and both sides
are alive, dispatch to ``skill_extras.tick_summons`` which walks each
summon's scheduled damage.

Runs *before* the solar/wither auras so a summon's hit and the actor's
aura can both land in the same round-end pulse.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="summons_tick", priority=30)
def _tick_summons(ctx: TurnContext) -> None:
    combatant = ctx.actor
    opponent = ctx.target
    if not (
        combatant.is_alive()
        and combatant.summons
        and opponent
        and opponent.is_alive()
    ):
        return
    # Lazy import — skill_extras has its own deep dependency graph.
    from ..skill_extras import tick_summons
    tick_summons(ctx.session, combatant, opponent)
