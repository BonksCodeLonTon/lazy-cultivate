"""Overheal-reservoir buffs — Mộc Linh Cộng Sinh.

A buff carrying an ``overheal_reservoir`` config block in its
``effect_overrides`` turns *wasted* overheal into a banked offensive pool.
Two halves, mirroring the aegis store-charge / discharge split:

  * **Capture** (``capture_overheal``) — called from ``session._apply_heal``
    after a heal is clamped at hp_max. The clamped-off remainder × ``capture_pct``
    accrues into the buff override's ``_sap`` reservoir (a config-only TOP-level
    key, like ``_stored_charge`` for aegis), capped at
    ``reservoir_cap_matk_scale × holder.matk``. Generic: every active buff with
    an ``overheal_reservoir`` block banks independently.

  * **Release** (PERIODIC hook, priority 48 — between wither=45 and regen=50) —
    releases ``release_pct`` of the banked ``_sap`` as an elemental strike on
    the opponent and decrements the bank by exactly what was released.

The ``_sap`` value lives at the override's TOP level (state), separate from the
``overheal_reservoir`` block (config) — same config/state separation as the
aegis ``_stored_charge`` vs ``_aegis`` split.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.balance import MAX_ELEMENTAL_RES
from src.game.engine.damage.color import colorize_damage

from .context import TurnContext
from .hooks import TurnPhase, register_hook

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


def _iter_overheal_buffs(holder: "Combatant"):
    """Yield ``(buff_key, override, config)`` for each active overheal buff."""
    for key in list(holder.effects.keys()):
        if holder.effects.get(key, 0) <= 0:
            continue
        ovr = holder.effect_overrides.get(key)
        if not ovr:
            continue
        cfg = ovr.get("overheal_reservoir")
        if isinstance(cfg, dict):
            yield key, ovr, cfg


def capture_overheal(holder: "Combatant", overheal_amount: int) -> None:
    """Bank a fraction of clamped-off overheal into every overheal buff.

    ``overheal_amount`` is the post-clamp remainder (the HP a heal could not
    apply because the holder was at / near hp_max). Each buff with an
    ``overheal_reservoir`` block captures ``capture_pct`` of it into its own
    ``_sap``, clamped at ``reservoir_cap_matk_scale × holder.matk``.
    """
    if overheal_amount <= 0:
        return
    for _key, ovr, cfg in _iter_overheal_buffs(holder):
        capture_pct = float(cfg.get("capture_pct", 0.0))
        if capture_pct <= 0:
            continue
        cap_scale = float(cfg.get("reservoir_cap_matk_scale", 0.0))
        cap = int(cap_scale * holder.matk)
        if cap <= 0:
            continue
        current = int(ovr.get("_sap", 0))
        ovr["_sap"] = min(cap, current + int(overheal_amount * capture_pct))


@register_hook(phase=TurnPhase.PERIODIC, name="overheal_release", priority=48)
def _overheal_release(ctx: TurnContext) -> None:
    holder = ctx.actor
    opponent = ctx.target
    if not (holder.is_alive() and opponent and opponent.is_alive()):
        return
    for _key, ovr, cfg in _iter_overheal_buffs(holder):
        sap = int(ovr.get("_sap", 0))
        if sap <= 0:
            continue
        release_pct = float(cfg.get("release_pct", 0.0))
        if release_pct <= 0:
            continue
        released = int(sap * release_pct)
        if released <= 0:
            continue
        ovr["_sap"] = sap - released
        element = str(cfg.get("element", "moc"))
        target_res = max(
            0.0,
            min(
                MAX_ELEMENTAL_RES,
                opponent.resistances.get(element, 0.0)
                - holder.element_pen.get(element, 0.0),
            ),
        )
        mult = 1.0 + holder.final_dmg_bonus
        strike = max(1, int(released * mult * (1.0 - target_res)))
        opponent.take_damage(strike)
        tag = colorize_damage(f"-{strike:,} HP", element)
        ctx.log.append(
            f"  🌿 **{holder.name}** Mộc Linh giải phóng Nhựa Linh "
            f"({released:,}) → **{opponent.name}** {tag}"
        )
