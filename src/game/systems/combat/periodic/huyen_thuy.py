"""Huyền Thủy Trường Sinh — Hồi Triều Nộ Hải tidal flood (PERIODIC).

L9 mechanic. Runs at priority 46 (alongside the overheal-reservoir release at
48; both are reservoir-discharge effects firing in the late-periodic band). Every
``thuy_tidal_flood_interval`` rounds the holder dumps a depth-scaled fraction of
its Tide reservoir onto the opponent as a Thủy strike, then keeps a fraction
(partial refill, not a full reset). The flood grows stronger the longer the fight
runs (``depth = 1 + turn × depth_per_turn``, capped) — the body's "the longer it
fights, the higher the tide rises" payoff.

Mirrors ``_overheal_release`` (the priority-48 reservoir discharge) and reuses
the shared ``tide_strike`` mitigation math. Inert for every other build: the gate
is the L9 ``thuy_tidal_flood_enabled`` flag + a non-empty reservoir + the cadence.
"""
from __future__ import annotations

from src.game.engine.damage.color import colorize_damage

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook
from ..thuy_tide import tide_strike


@register_hook(phase=TurnPhase.PERIODIC, name="huyen_thuy_tidal_flood", priority=46)
def _tidal_flood(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if not actor.thuy_tidal_flood_enabled:
        return
    interval = actor.thuy_tidal_flood_interval
    if interval <= 0 or ctx.turn <= 0 or ctx.turn % interval != 0:
        return
    if actor.thuy_intake_reservoir <= 0 or not (opponent and opponent.is_alive()):
        return

    # Depth scales with elapsed rounds, capped — "the longer it fights, the
    # higher the tide rises".
    depth = min(
        actor.thuy_tidal_depth_mult_cap,
        1.0 + ctx.turn * actor.thuy_tidal_depth_per_turn,
    )
    wave = int(actor.thuy_intake_reservoir * actor.thuy_tidal_release_pct * depth)
    dealt = tide_strike(actor, opponent, wave)
    if dealt <= 0:
        return
    # Partial keep — refill_pct of the reservoir survives the flood (not a full
    # reset), so successive floods still have a base to build from.
    actor.thuy_intake_reservoir = int(
        actor.thuy_intake_reservoir * actor.thuy_tidal_refill_pct
    )
    tag = colorize_damage(f"-{dealt:,} HP", "thuy")
    ctx.log.append(
        f"  🌊 **{actor.name}** Hồi Triều Nộ Hải — dội sóng Triều Khố "
        f"(×{depth:.1f}) → **{opponent.name}** {tag}"
    )
