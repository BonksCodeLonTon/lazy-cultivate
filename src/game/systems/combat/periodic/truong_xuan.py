"""Trường Xuân Linh Mộc — Trường Xuân Hồi Nguyên debuff-count regen (PERIODIC).

L6 mechanic. Each periodic phase the holder heals ``hp_max × per_debuff`` for
every ACTIVE debuff the opponent is carrying, up to ``moc_regen_debuff_cap``
ticks. The more pressure the Mộc body has stacked on the enemy (poison, slow,
bleed, marks…), the harder its spring renews — rewarding the body's debuff-heavy
playstyle.

Runs at priority 47, between the Thủy tidal flood (46) and the overheal
reservoir release (48). Inert for every other build: the gate is the L6
``moc_regen_per_enemy_debuff`` magnitude, which no other combatant carries.
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS, EffectKind

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="truong_xuan_hoi_nguyen", priority=47)
def _spring_renewal(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if actor.moc_regen_per_enemy_debuff <= 0 or not actor.is_alive():
        return
    if opponent is None:
        return

    # Count the opponent's distinct active debuff effects (one tick per effect,
    # NOT per stack). Mirrors the EFFECTS.get(key).kind enumeration the revive
    # cascade already uses over ``combatant.effects``.
    debuff_count = sum(
        1
        for key in opponent.effects
        if (meta := EFFECTS.get(key)) is not None and meta.kind == EffectKind.DEBUFF
    )
    capped = min(debuff_count, actor.moc_regen_debuff_cap)
    if capped <= 0:
        return

    heal = int(actor.hp_max * actor.moc_regen_per_enemy_debuff * capped)
    if heal <= 0 or actor.hp >= actor.hp_max:
        return
    applied = ctx.session._apply_heal(actor, heal)
    if applied > 0:
        ctx.log.append(
            f"  🌱 **{actor.name}** Trường Xuân Hồi Nguyên — hồi +{applied:,} HP "
            f"({capped} hiệu ứng bất lợi trên **{opponent.name}**)"
        )
