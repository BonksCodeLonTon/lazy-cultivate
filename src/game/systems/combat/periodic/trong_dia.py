"""Trọng Địa Khống Chế — Heavy Earth Control auto-slow (PERIODIC).

L6 mechanic of Kim Cang Bất Hoại Thể. Each periodic phase, when the
holder carries ``tho_auto_slow_enabled``, both DebuffTroBuoc and
DebuffLunDat are applied to the opponent via ``inflict_debuff`` — so
debuff immunity / ``debuff_immune_pct`` / slow-immunity still gate the
stamp as intended counterplay.

Runs at priority 30, alongside the summons tick. Inert for every other
build: the gate is ``tho_auto_slow_enabled`` which no other combatant
carries.
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS, get_combat_modifiers

from ..casting import inflict_debuff
from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="trong_dia_khong_che", priority=30)
def _trong_dia(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if not actor.tho_auto_slow_enabled:
        return
    if not actor.is_alive():
        return
    if opponent is None or not opponent.is_alive():
        return

    # Counterplay: ``debuff_immune_pct`` scales down the auto-slow exactly like
    # the skill path's ``_compute_effective_chance`` (base 100% × (1 − immune)),
    # so a fully debuff-immune target is never slow-locked. ``inflict_debuff``
    # already honors the hard ``slow_immune`` gate on top of this.
    immune = max(0.0, min(1.0, opponent.debuff_immune_pct
                          + float(get_combat_modifiers(opponent).get("debuff_immune_pct", 0.0))))
    apply_chance = 1.0 - immune

    for key in ("DebuffTroBuoc", "DebuffLunDat"):
        meta = EFFECTS.get(key)
        if meta is None:
            continue
        if apply_chance < 1.0 and ctx.session.rng.random() >= apply_chance:
            continue
        inflict_debuff(ctx.session, key, meta, opponent, actor=actor)
