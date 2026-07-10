"""Hỗn Độn Ma Thần Thể — L9 Khai Thiên Tích Địa form cadence (PRE_TURN).

Every ``hdm_form_interval`` acted turns the holder transforms into the Hỗn
Độn Chân Thân for the buff's 4-turn duration: BuffHonDonChanThan carries
+40% atk/def and +30% res_all as REAL stat modifiers, and the L3 HP-shockwave
rider reads the buff to multiply itself by ``hdm_form_rider_mult`` — the
sheet's "immune to all elemental damage" is CONVERTED to the capped res_all
lane per the no-0-damage-gate rule.

Inert for every other build (``hdm_form_interval`` defaults 0).
"""
from __future__ import annotations

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook

_FORM_KEY = "BuffHonDonChanThan"
_FORM_TURNS = 4


@register_hook(phase=TurnPhase.PRE_TURN, name="hon_don_ma_form", priority=42)
def _hon_don_ma_form(ctx: TurnContext) -> None:
    actor = ctx.actor
    if actor.hdm_form_interval <= 0 or not actor.is_alive():
        return
    if not actor.tick_cadence("hdm_form_turn_counter", actor.hdm_form_interval):
        return
    actor.apply_effect(_FORM_KEY, _FORM_TURNS)
    ctx.log.append(
        f"  🌀 **{actor.name}** KHAI THIÊN TÍCH ĐỊA — "
        f"hóa Hỗn Độn Chân Thân ({_FORM_TURNS} lượt)!"
    )
