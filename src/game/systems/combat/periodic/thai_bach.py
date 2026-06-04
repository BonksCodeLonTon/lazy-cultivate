"""Thái Bạch Canh Kim — Bạch Kim Phong Vũ stack growth (PERIODIC).

Runs at priority 25, right after the DoT loop (20) so the bleed it keys off
has already ticked this round. While the holder carries ``BuffBachKimPhongVu``
and their opponent is bleeding (≥1 bleed stack), the holder gains one Bạch Kim
stack per round (capped at 5 via ``_STACK_EFFECT_KEY``). The stack folds
+atk_pct / +bleed_dmg_bonus in through the buff's scaling_rules. Never decays.

Inert for every other build: the gate is the L1 buff + an opponent bleed, so a
combatant without the body never accrues stacks.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="thai_bach_bach_kim", priority=25)
def _grow_bach_kim(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if not actor.has_effect("BuffBachKimPhongVu"):
        return
    if opponent.bleed_stacks < 1:
        return
    gained = actor.add_stack("bach_kim", 1)
    if gained > 0:
        ctx.log.append(
            f"  🌪️ **{actor.name}** Bạch Kim Phong Vũ [×{actor.bach_kim_stacks}/5]"
        )
