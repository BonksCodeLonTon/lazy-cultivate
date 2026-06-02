"""Xuân Thu Nhất Bút — Spring-Autumn rotation refresh.

Skill ``SkillXuanThuNhatBut`` stamps the 4-turn buff
``BuffXuanThuLuanChuyen``; this hook rewrites the buff's
``effect_overrides.stat_bonus`` each turn so the active stat layer flips
between two "seasons" based on the buff's remaining duration.

Same idiom as :mod:`src.game.systems.combat.auras.luu_tinh`: the public
stat keys (``hp_regen_pct`` / ``final_dmg_reduce`` for Spring,
``final_dmg_bonus`` / ``crit_dmg_rating`` for Autumn) win per-stat in
``get_combat_modifiers``; the private config keys
(``_xt_spring_hp_regen_pct``, ``_xt_spring_final_dmg_reduce``,
``_xt_autumn_final_dmg_bonus``, ``_xt_autumn_crit_dmg_rating``) carry the
designer-tunable magnitudes and are popped by ``get_combat_modifiers``
so they never leak as real stats.

Season schedule for duration=4:

  remaining=4  → Spring  (turn 1)
  remaining=3  → Autumn  (turn 2)
  remaining=2  → Spring  (turn 3)
  remaining=1  → Autumn  (turn 4)

i.e. ``is_spring = (remaining % 2 == 0)`` — even remaining → Spring,
odd remaining → Autumn. Starts on Spring as the spec dictates.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="xuan_thu_rotation", priority=35)
def _refresh_xuan_thu_rotation(ctx: TurnContext) -> None:
    actor = ctx.actor
    buff_key = EffectKey.BUFF_XUAN_THU_LUAN_CHUYEN.value
    if not actor.has_effect(buff_key):
        return
    remaining = actor.effects.get(buff_key, 0)
    ovr = actor.effect_overrides.setdefault(buff_key, {})
    sb = ovr.setdefault("stat_bonus", {})
    spring_hp_regen = float(sb.get("_xt_spring_hp_regen_pct", 0.06))
    spring_dr = float(sb.get("_xt_spring_final_dmg_reduce", 0.10))
    spring_shield_regen = float(sb.get("_xt_spring_shield_regen_pct", 0.015))
    autumn_dmg = float(sb.get("_xt_autumn_final_dmg_bonus", 0.12))
    autumn_crit_dmg = float(sb.get("_xt_autumn_crit_dmg_rating", 200.0))
    autumn_crit = float(sb.get("_xt_autumn_crit_rating", 150.0))
    is_spring = (remaining % 2 == 0)
    if is_spring:
        sb["hp_regen_pct"] = spring_hp_regen
        sb["final_dmg_reduce"] = spring_dr
        sb["shield_regen_pct"] = spring_shield_regen
        sb.pop("final_dmg_bonus", None)
        sb.pop("crit_dmg_rating", None)
        sb.pop("crit_rating", None)
    else:
        sb["final_dmg_bonus"] = autumn_dmg
        sb["crit_dmg_rating"] = autumn_crit_dmg
        sb["crit_rating"] = autumn_crit
        sb.pop("hp_regen_pct", None)
        sb.pop("final_dmg_reduce", None)
        sb.pop("shield_regen_pct", None)
