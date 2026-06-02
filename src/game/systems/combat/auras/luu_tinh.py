"""Lưu Tinh Cản Nguyệt — dynamic evasion/spd refresh from opponent fire DoTs.

Extracted from ``CombatSession._refresh_luu_tinh_can_nguyet``.

The aura base ``spd_pct: 0.20`` lives on the EffectMeta (always-on while
the aura is active). The DYNAMIC layer — ``+150 Né Tránh`` and ``+5% Tốc
Độ`` per distinct fire DoT on the opponent — is rewritten each turn into
the holder's ``effect_overrides`` under ``stat_bonus.evasion_rating`` and
``stat_bonus.spd_pct``. The override magnitude wins over the meta default
per stat (``get_combat_modifiers`` standard behavior), so we always
preserve the base 0.20 by writing ``0.20 + 0.05 × count``.

Magnitudes are tunable per-skill via the cast's effect_overrides:
``_lt_base_spd_pct``, ``_lt_per_dot_evasion``, ``_lt_per_dot_spd_pct``.
These config keys are popped from the aggregated stat dict by the
cleanup pass in ``effects.get_combat_modifiers`` so they don't leak.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.engine.effects import count_elemental_dots

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="luu_tinh_can_nguyet", priority=30)
def _refresh_luu_tinh_can_nguyet(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    buff_key = EffectKey.BUFF_LUU_TINH_CAN_NGUYET.value
    if not actor.has_effect(buff_key):
        return
    fire_dots = count_elemental_dots(opponent, "hoa")
    ovr = actor.effect_overrides.setdefault(buff_key, {})
    sb = ovr.setdefault("stat_bonus", {})
    base_spd = float(sb.get("_lt_base_spd_pct", 0.20))
    per_dot_eva = float(sb.get("_lt_per_dot_evasion", 150.0))
    per_dot_spd = float(sb.get("_lt_per_dot_spd_pct", 0.05))
    sb["spd_pct"] = base_spd + per_dot_spd * fire_dots
    sb["evasion_rating"] = per_dot_eva * fire_dots
