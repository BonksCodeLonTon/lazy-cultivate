"""Phù Dao Trực Thượng — altitude tier bump.

Extracted from the inline ``BuffPhuDao`` block at the bottom of the
pre-turn chain in ``CombatSession._take_turn``.

The actor riding the whirlwind climbs one altitude tier per turn they
actually act, capped at the skill's ``altitude_max``. The legacy block
fired AFTER the CC/silence early-returns so a stunned/silenced holder
gained no altitude that turn (and ``reset_on_cc`` already zeroed them
when the CC landed). Same gating preserved here — the hook is on the
PRE_TURN phase, which runs only after ``_take_turn`` has cleared CC /
silence early-returns.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="phu_dao_altitude", priority=60)
def _phu_dao_altitude_tick(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.has_effect("BuffPhuDao"):
        return
    ovr = actor.effect_overrides.get("BuffPhuDao", {})
    altitude_max = int(ovr.get("altitude_max", 4))
    if actor.phu_dao_altitude >= altitude_max:
        return
    actor.phu_dao_altitude += 1
    ctx.log.append(
        f"  🪁 **{actor.name}** **Phù Dao** "
        f"[Cao Độ ×{actor.phu_dao_altitude}/{altitude_max}]"
    )
