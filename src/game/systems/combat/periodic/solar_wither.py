"""Tier-passive auras — Thái Dương Thần Quang + Khô Mộc Hấp Thu.

Two PERIODIC hooks, one per aura:

  * ``solar_aura`` (priority 40) — Thái Dương Thần Thể tier passive.
    Every turn, deal fire damage to the opponent equal to
    ``hp_max × solar_aura_pct``, boosted by final_dmg_bonus +
    dot_dmg_bonus_by_kind["burn"], with bonus_dmg_vs_burn vs already-
    burning targets and element_pen reducing the target's hoa resistance.
    Independent of skill actions and DoT ticks.

  * ``wither_aura`` (priority 45) — Khô Mộc Thần Thể tier passive.
    Drains ``hp_max × wither_aura_pct`` from the opponent as Mộc
    damage, then heals the holder by the same amount (routed through
    session._apply_heal so heal_can_crit / bleed-heal-reduction /
    heal-to-damage queue all behave correctly).

Both bypass crit/evasion/ATK scaling because they're deterministic
side effects of a passive, not fresh skill casts.
"""
from __future__ import annotations

from src.game.constants.balance import MAX_ELEMENTAL_RES
from src.game.engine.damage.color import colorize_damage

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="solar_aura", priority=40)
def _solar_aura(ctx: TurnContext) -> None:
    combatant = ctx.actor
    opponent = ctx.target
    if not (
        combatant.is_alive()
        and combatant.solar_aura_pct > 0
        and opponent
        and opponent.is_alive()
    ):
        return
    base = int(combatant.hp_max * combatant.solar_aura_pct)
    if base <= 0:
        return
    mult = (
        1.0 + combatant.final_dmg_bonus
        + float(combatant.dot_dmg_bonus_by_kind.get("burn", 0.0))
    )
    if opponent.burn_stacks > 0 and combatant.bonus_dmg_vs_burn > 0:
        mult += combatant.bonus_dmg_vs_burn
    target_res = max(
        0.0,
        min(
            MAX_ELEMENTAL_RES,
            opponent.resistances.get("hoa", 0.0) - combatant.element_pen.get("hoa", 0.0),
        ),
    )
    aura_dmg = max(1, int(base * mult * (1.0 - target_res)))
    opponent.take_damage(aura_dmg)
    aura_tag = colorize_damage(f"-{aura_dmg:,} HP", "hoa")
    ctx.log.append(
        f"  ☀️ **{combatant.name}** Thái Dương Thần Quang → "
        f"**{opponent.name}** {aura_tag}"
    )


@register_hook(phase=TurnPhase.PERIODIC, name="wither_aura", priority=45)
def _wither_aura(ctx: TurnContext) -> None:
    combatant = ctx.actor
    opponent = ctx.target
    if not (
        combatant.is_alive()
        and combatant.wither_aura_pct > 0
        and opponent
        and opponent.is_alive()
    ):
        return
    base = int(combatant.hp_max * combatant.wither_aura_pct)
    if base <= 0:
        return
    mult = 1.0 + combatant.final_dmg_bonus + combatant.dot_dmg_bonus
    target_res = max(
        0.0,
        min(
            MAX_ELEMENTAL_RES,
            opponent.resistances.get("moc", 0.0) - combatant.element_pen.get("moc", 0.0),
        ),
    )
    drain_dmg = max(1, int(base * mult * (1.0 - target_res)))
    opponent.take_damage(drain_dmg)
    healed = ctx.session._apply_heal(combatant, drain_dmg)
    drain_tag = colorize_damage(f"-{drain_dmg:,} HP", "moc")
    ctx.log.append(
        f"  🌿 **{combatant.name}** Khô Mộc Hấp Thu → "
        f"**{opponent.name}** {drain_tag} (+{healed:,} HP)"
    )
