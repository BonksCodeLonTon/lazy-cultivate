"""Hoàng Cổ Thánh Thể — PRE_TURN periodic-crit cadence and Saint Realm (PRE_TURN).

Two mechanics ride one hook:

L6 Thiên Tinh Quán Đỉnh — every ``saint_periodic_crit_interval`` acted turns,
arm a guaranteed crit (``saint_crit_armed = True``). The arm is consumed on
the first landed damaging hit (casting.py). Mirrors body #2's Thái Bạch arm
pattern exactly — same counter-increment, same modulo gate, parallel flag.

L9 Thần Tâm Thánh Cốt — every ``saint_realm_interval`` acted turns, self-apply
``BuffHoangCoThanhVuc`` for ``saint_realm_duration`` turns. While the buff is
active, combat_hit.py forces crit and inflict_interceptors.py blocks hard CC.

Both cadences track acted-turns only: this hook fires in PRE_TURN which
``_take_turn`` dispatches AFTER the CC-skip / phase-lock / dodge returns, so a
stunned turn never bumps the counters.

Inert for every other build: the gate is the L9 marker buff + non-zero interval
for realm, and non-zero interval for L6 crit — neither fires on enemies or
flag-off / non-saint players.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="hoang_co_saint_cadence", priority=38)
def _saint_cadence(ctx: TurnContext) -> None:
    actor = ctx.actor

    # ── L6 Thiên Tinh Quán Đỉnh — every-N-turn guaranteed crit ──────────────
    if actor.tick_cadence("saint_crit_turn_counter", actor.saint_periodic_crit_interval):
        actor.saint_crit_armed = True
        ctx.log.append(
            f"  ⭐ **{actor.name}** Thiên Tinh Quán Đỉnh — đòn kế tiếp chắc chắn Bạo Kích!"
        )

    # ── L9 Thần Tâm Thánh Cốt — every-N-turn Saint Realm window ─────────────
    if not actor.saint_realm_enabled:
        return
    if actor.tick_cadence("saint_realm_turn_counter", actor.saint_realm_interval):
        duration = max(1, actor.saint_realm_duration)
        actor.apply_effect("BuffHoangCoThanhVuc", duration)
        ctx.log.append(
            f"  🌌 **{actor.name}** Hoàng Cổ Thánh Vực bùng phát "
            f"({duration} lượt) — Bạo Kích tuyệt đối + miễn khống chế!"
        )
