"""Liệt Diễm Phần Thiên — escalating-fire ramp (PERIODIC).

One PERIODIC hook (priority 26, just after chan_duong@25) driving the L1
time-scaling each round:

  **L1 Phần Thiên Chi Nộ** — ``lietdiem_burn_stacks`` climbs
  +``lietdiem_burn_per_turn`` up to ``lietdiem_burn_cap`` and NEVER decays.
  BuffPhanThienChiNo's scaling_rules read the counter (``stat:lietdiem_burn_stacks``)
  and convert it into matk_pct + crit_rating, so the stat side needs no code
  here — only the counter (mirrors the harmony body's nhan_hoa incrementer,
  also PERIODIC).

The L9 Hỏa Thần avatar cadence lives in ``auras/lietdiem.py`` (PRE_TURN) so
its 2-turn window covers two *acted* turns and the counter tracks acted turns
only — mirroring the saint-realm cadence in ``auras/hoang_co.py``.

Inert for every other build: the gate is a per-body config flag, 0 on every
non-Liệt-Diễm combatant.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="lietdiem", priority=26)
def _lietdiem(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.is_alive():
        return

    # L1 ramp — Phần Thiên climbs (capped, no decay).
    if actor.lietdiem_burn_per_turn > 0 and actor.lietdiem_burn_cap > 0:
        if actor.lietdiem_burn_stacks < actor.lietdiem_burn_cap:
            actor.lietdiem_burn_stacks = min(
                actor.lietdiem_burn_cap,
                actor.lietdiem_burn_stacks + actor.lietdiem_burn_per_turn,
            )
            ctx.log.append(
                f"  🔥 **{actor.name}** Phần Thiên Chi Nộ "
                f"[×{actor.lietdiem_burn_stacks}/{actor.lietdiem_burn_cap}]"
            )
