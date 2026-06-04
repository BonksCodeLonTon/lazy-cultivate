"""Huyền Âm Thiên Ma — auto-Nhập-Ma cadence (PRE_TURN).

Ma Đạo Hóa Thần (L9): every Nth acted turn the holder auto-enters the Nhập Ma
trance (self-applies ``BuffNhapMa`` for ``nhap_ma_duration`` turns), which in
turn unlocks the L6 Thiên Ma Đồng Hóa payoffs (+final damage + on-hit
stat-steal). Runs in the PRE_TURN phase, dispatched by ``_take_turn`` AFTER the
CC-skip / phase-lock / dodge returns, so a turn lost to a stun never bumps the
counter — the cadence tracks *acted* turns only (mirrors body #2's pattern).

Inert for every other build: the gate is the L9 marker buff + a non-zero
interval, neither of which any other combatant carries.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="huyen_am_auto_nhap_ma", priority=40)
def _auto_nhap_ma(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.has_effect("BuffMaDaoHoaThan"):
        return
    interval = actor.nhap_ma_interval
    if interval <= 0:
        return
    actor.nhap_ma_turn_counter += 1
    if actor.nhap_ma_turn_counter % interval == 0:
        actor.apply_effect("BuffNhapMa", max(1, actor.nhap_ma_duration))
        ctx.log.append(
            f"  👹 **{actor.name}** Ma Đạo Hóa Thần — tự động **Nhập Ma** "
            f"({actor.nhap_ma_duration} lượt)!"
        )
