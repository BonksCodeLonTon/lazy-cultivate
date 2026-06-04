"""Chân Dương Bất Diệt — Hỏa Khí Tương Sinh burning crit-ramp (PERIODIC).

Runs at priority 25, right after the DoT loop (20) so the burn it keys off has
already ticked this round. While the holder carries ``BuffHoaKhiTuongSinh`` and
their opponent is burning (≥1 burn stack), ``burning_crit_stacks`` grows by 1
per round, capped at 10 (the buff's scaling rule grants +20 crit_rating/stack,
``max_output`` 200 = 10 stacks). When the opponent's burn drops, the ramp
resets to 0 — the symbiosis only holds while the enemy is on fire.

Manually capped here (not via ``add_stack`` / ``_STACK_EFFECT_KEY``) because the
reset-on-burn-drop is bespoke. Inert for every other build: the gate is the L1
buff + an opponent burn, so a combatant without the body never ramps.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook

_BURNING_CRIT_CAP = 10


@register_hook(phase=TurnPhase.PERIODIC, name="chan_duong_burning_crit", priority=25)
def _grow_burning_crit(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if not actor.has_effect("BuffHoaKhiTuongSinh"):
        return
    if opponent.burn_stacks < 1:
        # Symbiosis broken — the enemy stopped burning, the ramp dissipates.
        actor.burning_crit_stacks = 0
        return
    if actor.burning_crit_stacks < _BURNING_CRIT_CAP:
        actor.burning_crit_stacks += 1
        ctx.log.append(
            f"  🔥 **{actor.name}** Hỏa Khí Tương Sinh "
            f"[×{actor.burning_crit_stacks}/{_BURNING_CRIT_CAP}]"
        )
