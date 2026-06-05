"""Đại Địa Phản Phệ — Great Earth Retribution aura (PERIODIC).

L9 mechanic of Kim Cang Bất Hoại Thể. Every turn the holder deals
``int(actor.shield × tho_earth_aura_shield_pct)`` Thổ-element damage
to the opponent. The hit respects the opponent's Thổ resistance exactly
as the solar aura respects Hỏa resistance. No crit / evasion roll —
this is a deterministic passive, identical semantics to solar_wither.py.

Runs at priority 42, between the solar (40) and wither (45) auras.
Inert for every other build: the gate is ``tho_earth_aura_shield_pct``
which defaults to 0.0 on every non-Thổ-L9 combatant.
"""
from __future__ import annotations

from src.game.constants.balance import MAX_ELEMENTAL_RES
from src.game.engine.damage.color import colorize_damage

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="dai_dia_phan_phe", priority=42)
def _dai_dia_aura(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if not (
        actor.is_alive()
        and actor.tho_earth_aura_shield_pct > 0
        and actor.shield > 0
        and opponent
        and opponent.is_alive()
    ):
        return
    base = int(actor.shield * actor.tho_earth_aura_shield_pct)
    if base <= 0:
        return
    mult = 1.0 + actor.final_dmg_bonus
    target_res = max(
        0.0,
        min(
            MAX_ELEMENTAL_RES,
            opponent.resistances.get("tho", 0.0) - actor.element_pen.get("tho", 0.0),
        ),
    )
    aura_dmg = max(1, int(base * mult * (1.0 - target_res)))
    opponent.take_damage(aura_dmg)
    aura_tag = colorize_damage(f"-{aura_dmg:,} HP", "tho")
    ctx.log.append(
        f"  🏔️ **{actor.name}** Đại Địa Phản Phệ → "
        f"**{opponent.name}** {aura_tag}"
    )
