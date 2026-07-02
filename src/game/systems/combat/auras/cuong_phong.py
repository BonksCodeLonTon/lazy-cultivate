"""Cửu Thiên Cương Phong Thể — Cương Phong storm cadence (PRE_TURN).

L9: every ``cp_storm_interval`` acted turns, self-apply ``BuffCuongPhongBao``
for ``cp_storm_duration`` turns. While the storm rages:

  * every attack is unevadable (casting.py bypass injection),
  * every hit force-crits (combat_hit force_crit OR-chain),
  * +``cp_storm_extra_hits`` strikes per cast (casting.py hit fold),
  * each hit rolls ``cp_storm_cuon_bay_chance`` Cuốn Bay
    (run_cuong_phong_procs).

Mirrors the Hoàng Cổ / Cửu Thiên / Tiêu Dao tick_cadence pattern — acted
turns only, a stunned turn never advances the counter. Inert for every other
build (interval 0).
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="cuong_phong_storm_cadence", priority=34)
def _cuong_phong_storm_cadence(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.tick_cadence("cp_storm_turn_counter", actor.cp_storm_interval):
        return
    duration = max(1, actor.cp_storm_duration)
    actor.apply_effect("BuffCuongPhongBao", duration)
    ctx.log.append(
        f"  🌪️ **{actor.name}** CƯƠNG PHONG NỔI BÃO ({duration} lượt) — "
        f"không đòn nào trượt, không đòn nào không bạo!"
    )
