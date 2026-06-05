"""Shield + HP + MP regeneration and Thổ Linh Căn shield check.

One PERIODIC hook (priority 50) that runs four logically-related ticks:

  1. **Thổ Linh Căn shield check** — ``lc_effects.check_shield``
     activates the low-HP defensive shield when conditions are met.
  2. **Shield recharge pause decrement** — the PoE-style pause counter
     ticks down each periodic phase; while > 0, ``shield_regen_*``
     is skipped so a recently-hit defender doesn't get free shield.
  3. **Shield regen** — once the pause elapses, recharge by
     ``shield_regen_pct × shield_cap + shield_regen_flat`` (with active
     buff layer mixed in via ``get_combat_modifiers``).
  4. **HP + MP regen** — pct (of *_max) + flat, stacked with active
     buff modifiers. Negative aggregates zero out instead of draining.
"""
from __future__ import annotations

from src.game.engine import linh_can_effects as lc_effects
from src.game.engine.effects import get_combat_modifiers

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="regen", priority=50)
def _regen(ctx: TurnContext) -> None:
    combatant = ctx.actor
    session = ctx.session

    # Periodic: Thổ Linh Căn — activate shield when HP is low.
    lc_effects.check_shield(combatant, ctx.log)

    # Energy Shield recharge (PoE-style): the pause counter is decremented
    # each periodic phase; while > 0, ``shield_regen_pct/flat`` are skipped
    # so a recently-hit defender doesn't get free shield instantly. After
    # the pause elapses, regen ticks normally up to ``shield_cap``.
    if combatant.shield_recharge_pause > 0:
        combatant.shield_recharge_pause -= 1

    # Active-buff layer for shield_regen_pct (e.g. Quang Minh Tung Hoành
    # Bộ active stamps +0.5% on top of any static investment).
    shield_regen_mods = get_combat_modifiers(combatant)
    effective_shield_regen_pct = (
        combatant.shield_regen_pct + shield_regen_mods.get("shield_regen_pct", 0.0)
    )
    if (
        combatant.is_alive()
        and combatant.shield_recharge_pause == 0
        and (effective_shield_regen_pct > 0 or combatant.shield_regen_flat > 0)
    ):
        # ``shield_regen_pct`` scales off the holder's own shield_cap so
        # high-shield builds regenerate proportionally to their investment.
        # ``shield_regen_flat`` adds on top.
        regen = (
            int(combatant.shield_cap() * effective_shield_regen_pct)
            + combatant.shield_regen_flat
        )
        gained = combatant.add_shield(regen)
        if gained > 0:
            ctx.log.append(
                f"  🪨 **{combatant.name}** Hộ Thuẫn hồi +{gained:,} khiên "
                f"({combatant.shield:,}/{combatant.shield_cap():,})"
            )
            # Kim Cang Bất Hoại — Địa Mạch: each shield-regen tick that
            # actually grants shield earns one stack (cap 6). The scaling
            # buff BuffDaiDiaCanCo reads ``dia_mach_stacks`` via
            # ``stat:dia_mach_stacks`` scaling_rules to grow shield_max_pct
            # and shield_regen_pct proportionally.
            if combatant.dia_mach_per_regen and combatant.dia_mach_stacks < 6:
                combatant.dia_mach_stacks = min(6, combatant.dia_mach_stacks + 1)
                ctx.log.append(
                    f"    🪨 **{combatant.name}** Địa Mạch "
                    f"[×{combatant.dia_mach_stacks}/6]"
                )

    # HP regen: pct (of hp_max) + flat, stacked.
    if not combatant.is_alive():
        return

    mods = get_combat_modifiers(combatant)
    effective_regen_pct = combatant.hp_regen_pct + mods.get("hp_regen_pct", 0.0)
    hp_pct_regen = int(combatant.hp_max * effective_regen_pct) if effective_regen_pct > 0 else 0
    hp_total_regen = hp_pct_regen + max(0, combatant.hp_regen_flat)
    if hp_total_regen > 0 and combatant.hp < combatant.hp_max:
        applied = session._apply_heal(combatant, hp_total_regen)
        if applied > 0:
            ctx.log.append(f"  💚 **{combatant.name}** hồi sinh lực +{applied} HP")

    # MP regen: pct (of mp_max) + flat, stacked.
    # Mirrors HP regen: stat_bonus modifiers (e.g. DebuffLinhLucKiet's
    # mp_regen_pct: -0.50) apply on top of the base. Negative aggregate
    # zeroes the regen rather than draining MP.
    effective_mp_regen_pct = max(
        0.0, combatant.mp_regen_pct + mods.get("mp_regen_pct", 0.0)
    )
    mp_pct_regen = int(combatant.mp_max * effective_mp_regen_pct) if effective_mp_regen_pct > 0 else 0
    mp_total_regen = mp_pct_regen + max(0, combatant.mp_regen_flat)
    if mp_total_regen > 0 and combatant.mp < combatant.mp_max:
        mp_total_regen = max(1, mp_total_regen)
        combatant.mp = min(combatant.mp_max, combatant.mp + mp_total_regen)
        ctx.log.append(f"  💙 **{combatant.name}** hồi linh lực +{mp_total_regen} MP")
