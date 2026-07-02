"""Tiêu Dao Thần Thể — Hóa Bằng / Hóa Côn form cadence (PRE_TURN).

L9: every ``td_form_interval`` acted turns, auto-transform for
``td_form_duration`` turns (+1 per ``td_canh_per_turn`` Tiêu Dao Cảnh stacks,
capped at +2). The form is picked by state — combat is automatic, so the
sheet's "choose one on activate" becomes an HP-gate rule:

  * HP below ``td_con_hp_gate`` of max → **Hóa Côn** (defensive): the buff's
    +60% DR soaks, take_damage banks every HP lost, and periodic/expiry.py
    releases the bank as capped true damage + a heal when the form ends.
  * otherwise → **Hóa Bằng** (offensive): spd +100% via the buff, force-crit
    (combat_hit OR-chain), +1 extra hit and unevadable attacks (casting).

Mirrors the Hoàng Cổ / Cửu Thiên tick_cadence pattern — acted turns only, so
a stunned turn never advances the counter. Inert for every other build.
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook

# Stack-driven duration bonus is capped so a long fight can't stretch the
# form window past half its cadence (3 base + 2 = 5 of every 8 turns).
_TD_FORM_BONUS_CAP = 2


@register_hook(phase=TurnPhase.PRE_TURN, name="tieu_dao_form_cadence", priority=37)
def _tieu_dao_form_cadence(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.tick_cadence("td_form_turn_counter", actor.td_form_interval):
        return
    duration = max(1, actor.td_form_duration)
    if actor.td_canh_per_turn > 0:
        duration += min(_TD_FORM_BONUS_CAP, actor.td_canh_stacks // actor.td_canh_per_turn)
    if actor.hp < int(actor.hp_max * actor.td_con_hp_gate):
        actor.td_con_bank = 0  # fresh soak — the bank belongs to THIS form
        actor.apply_effect("BuffHoaCon", duration)
        ctx.log.append(
            f"  🐋 **{actor.name}** HÓA CÔN ({duration} lượt) — Huyền Côn lặn "
            f"Bắc Minh, nuốt trọn cuồng phong!"
        )
    else:
        actor.apply_effect("BuffHoaBang", duration)
        ctx.log.append(
            f"  🦅 **{actor.name}** HÓA BẰNG ({duration} lượt) — Đại Bằng "
            f"giương cánh chín vạn dặm!"
        )
