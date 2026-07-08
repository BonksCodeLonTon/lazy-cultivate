"""Thái Dương Đạo Thể — L9 Nhật Diệu Cửu Thiên solar burst (PRE_TURN).

Every ``td_solar_interval`` acted turns the holder radiates Nhật Quang:
capped true damage = ``td_solar_hp_pct`` × the holder's OWN max HP (through
the shared ``_deal_capped_true_dmg`` tail — the 12% target-max-HP clamp keeps
a giant furnace tank from one-shotting), plus a 3-turn Lóa Mắt on the
opponent (through ``inflict_debuff`` so blind-immunity/shrug gates apply).

Mirrors the tick_cadence PRE_TURN pattern — acted turns only. Inert for
every other build (``td_solar_interval`` defaults 0).
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook

_SOLAR_BLIND_TURNS = 3


@register_hook(phase=TurnPhase.PRE_TURN, name="thai_duong_solar", priority=40)
def _thai_duong_solar(ctx: TurnContext) -> None:
    actor = ctx.actor
    target = ctx.target
    session = ctx.session
    if actor.td_solar_interval <= 0 or not target.is_alive():
        return
    if not actor.tick_cadence("td_solar_turn_counter", actor.td_solar_interval):
        return
    from ..casting import _deal_capped_true_dmg, inflict_debuff

    ctx.log.append(f"  🌞 **{actor.name}** NHẬT DIỆU CỬU THIÊN — nhật quang tỏa vực!")
    burst = int(actor.hp_max * actor.td_solar_hp_pct)
    if burst > 0:
        _deal_capped_true_dmg(session, target, burst, "Nhật Quang")
    _lm = EFFECTS.get("DebuffLoaMat")
    if _lm is not None and target.is_alive():
        inflict_debuff(
            session, "DebuffLoaMat", _lm, target, actor=actor,
            overrides={"duration": _SOLAR_BLIND_TURNS},
        )
