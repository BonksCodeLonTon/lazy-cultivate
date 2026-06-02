"""Liễu Nhứ Tùy Phong — Mộc-DoT drift refresh.

Extracted from ``CombatSession._refresh_lieu_nhu_tuy_phong``.

The aura's ``hp_regen_pct`` is static (lives on the EffectMeta,
always on). The spd / evasion layer is OPPONENT-conditional: it
only applies while the opponent carries a Mộc-element DoT, and
doubles while the 3-turn active buff is up. Rewritten each turn into
the aura's ``effect_overrides.stat_bonus`` (override wins per-stat in
``get_combat_modifiers``) — same idiom as
:mod:`src.game.systems.combat.auras.luu_tinh`.

The gate accepts ANY active moc DoT — stack (Độc Tố), caster-stat,
OR target-%HP (Phệ Huyết Thực / Hủ Mộc) — so the whole Mộc DoT kit
feeds it (broader than ``count_elemental_dots``, which skips
``dot_target_hp_pct`` DoTs). Base magnitudes are per-skill-tunable via
the cast's effect_overrides (``_ln_base_spd_pct`` / ``_ln_base_evasion``);
both are popped by ``get_combat_modifiers`` so they never leak as real
stats.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="lieu_nhu_tuy_phong", priority=40)
def _refresh_lieu_nhu_tuy_phong(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    aura_key = EffectKey.BUFF_LIEU_NHU_TUY_PHONG_AURA.value
    if not actor.has_effect(aura_key):
        return
    has_moc_dot = any(
        (m := EFFECTS.get(k)) is not None
        and m.dot_element == "moc"
        and (
            m.dot_pct > 0
            or m.stack_kind
            or m.dot_caster_hp_pct > 0
            or m.dot_caster_matk_scale > 0
            or m.dot_target_hp_pct > 0
        )
        for k in opponent.effects
    )
    ovr = actor.effect_overrides.setdefault(aura_key, {})
    sb = ovr.setdefault("stat_bonus", {})
    base_spd = float(sb.get("_ln_base_spd_pct", 0.15))
    base_eva = float(sb.get("_ln_base_evasion", 400.0))
    mult = 2.0 if actor.has_effect(
        EffectKey.BUFF_LIEU_NHU_TUY_PHONG.value
    ) else 1.0
    if has_moc_dot:
        sb["spd_pct"] = base_spd * mult
        sb["evasion_rating"] = base_eva * mult
    else:
        sb["spd_pct"] = 0.0
        sb["evasion_rating"] = 0.0
