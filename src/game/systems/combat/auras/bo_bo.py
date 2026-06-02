"""Bộ Bộ Sinh Liên — heal-taken → mobility refresh.

Extracted from ``CombatSession._refresh_bo_bo_sinh_lien``.

Pure self-scaling: each 1% of the holder's aggregated
``heal_taken_bonus`` grants +1% spd and +25 evasion, capped (default
+30% spd / +750 evasion). Rewritten each turn into the aura's
``effect_overrides.stat_bonus`` — same idiom as
:mod:`src.game.systems.combat.auras.lieu_nhu`. No scaling-rule source
exists for ``heal_taken_bonus`` so this can't be expressed as data.

Reading ``get_combat_modifiers`` here is recursion-safe: the aura only
ever emits spd/evasion, never ``heal_taken_bonus``, so the value read
is independent of what this hook writes. Ratios/caps are
per-skill-tunable via effect_overrides (``_bb_*``); popped by
``get_combat_modifiers`` so they never leak as real stats.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.engine.effects import get_combat_modifiers

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="bo_bo_sinh_lien", priority=50)
def _refresh_bo_bo_sinh_lien(ctx: TurnContext) -> None:
    actor = ctx.actor
    aura_key = EffectKey.BUFF_BO_BO_SINH_LIEN_AURA.value
    if not actor.has_effect(aura_key):
        return
    ovr = actor.effect_overrides.setdefault(aura_key, {})
    sb = ovr.setdefault("stat_bonus", {})
    spd_per = float(sb.get("_bb_spd_per_htb", 1.0))
    eva_per = float(sb.get("_bb_eva_per_htb", 2500.0))
    spd_cap = float(sb.get("_bb_spd_cap", 0.30))
    eva_cap = float(sb.get("_bb_eva_cap", 750.0))
    htb = max(
        0.0,
        float(get_combat_modifiers(actor).get("heal_taken_bonus", 0.0)),
    )
    sb["spd_pct"] = min(spd_cap, htb * spd_per)
    sb["evasion_rating"] = min(eva_cap, htb * eva_per)
