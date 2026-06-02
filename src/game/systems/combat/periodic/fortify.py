"""Hào Quang Củng Cố stack gain + Fortify brace decrement.

One PERIODIC hook (priority 60). Gain one stack each periodic phase
(Hoang Cổ Thánh Thể Chain 9), capped at ``fortify_stack_cap``; no-op
when the holder doesn't carry the aura. The post-hit brace
``fortify_braced_turns`` is decremented AFTER combat resolution so the
brace covers the swing that arrived this turn — once the periodic
phase fires, the brace expires.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="fortify", priority=60)
def _fortify(ctx: TurnContext) -> None:
    combatant = ctx.actor

    if combatant.is_alive() and combatant.fortify_per_turn_pct > 0:
        cap = combatant.fortify_stack_cap or 0
        if cap > 0 and combatant.fortify_stacks < cap:
            combatant.fortify_stacks += 1
            bonus = combatant.fortify_per_turn_pct * combatant.fortify_stacks
            ctx.log.append(
                f"  🛡️ **{combatant.name}** Hào Quang Củng Cố [×{combatant.fortify_stacks}/{cap}] "
                f"(+{bonus * 100:.1f}% ST cuối / Giảm ST)"
            )
    if combatant.fortify_braced_turns > 0:
        combatant.fortify_braced_turns -= 1
