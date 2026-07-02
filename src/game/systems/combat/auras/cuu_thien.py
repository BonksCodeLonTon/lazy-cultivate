"""Cửu Thiên Huyền Lôi Thể — Thần Lôi Diệt Thế burst cadence (PRE_TURN).

L9: every ``ct_burst_interval`` acted turns, self-apply ``BuffThanLoiGiangThe``
for ``ct_burst_duration`` turns (+1 turn when Năng Lượng is at
``ct_burst_bonus_turn_gate``). While the buff is active:

  * spd +100% / Lôi dmg +50% — live via the buff's stat_bonus,
  * every hit force-crits — combat_hit.py's force_crit OR-chain,
  * every hit auto-applies Sốc Điện + Sét Đánh and rolls the boosted
    ``ct_burst_te_liet_chance`` Tê Liệt — run_cuu_thien_procs.

Mirrors the Hoàng Cổ Saint Realm cadence exactly (``tick_cadence`` counts
acted turns only — a stunned turn never advances the counter). Inert for
every other build: gate is a non-zero interval.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="cuu_thien_burst_cadence", priority=36)
def _cuu_thien_burst_cadence(ctx: TurnContext) -> None:
    actor = ctx.actor
    if actor.tick_cadence("ct_burst_turn_counter", actor.ct_burst_interval):
        duration = max(1, actor.ct_burst_duration)
        if (
            actor.ct_burst_bonus_turn_gate > 0
            and actor.ct_nang_luong_stacks >= actor.ct_burst_bonus_turn_gate
        ):
            duration += 1
        actor.apply_effect("BuffThanLoiGiangThe", duration)
        ctx.log.append(
            f"  🌩️ **{actor.name}** THẦN LÔI GIÁNG THẾ ({duration} lượt) — "
            f"tốc độ ×2, Lôi +50%, Bạo Kích tuyệt đối!"
        )
