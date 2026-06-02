"""Lục Dục Thiên Ma Vũ — auto-cycling six-desires passive.

One PERIODIC hook (priority 90). Driven by skill ownership rather than
aura plumbing so the cycle survives a cleanse on the marker.

Phases:
  * **gaining** → +2 desires per tick until 6 are active
  * **amp**     → all 6 + Cộng Minh marker, lasts one acting turn
  * **expired** → 2 silent ticks with everything cleared
  * → restart at gaining
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


_LUC_DUC_DESIRE_KEYS: tuple[str, ...] = (
    "BuffLucDucSac", "BuffLucDucThanh", "BuffLucDucHuong",
    "BuffLucDucVi", "BuffLucDucXuc", "BuffLucDucPhap",
)


@register_hook(phase=TurnPhase.PERIODIC, name="luc_duc_thien_ma_vu", priority=90)
def _luc_duc(ctx: TurnContext) -> None:
    combatant = ctx.actor
    if "SkillAmLucDucThienMaVu_R9" not in combatant.skill_keys:
        return

    if combatant.luc_duc_expire_turns_left > 0:
        combatant.luc_duc_expire_turns_left -= 1
        return

    amp_key = "BuffLucDucCongMinh"
    active_desires = [
        k for k in _LUC_DUC_DESIRE_KEYS if combatant.has_effect(k)
    ]
    amp_active = combatant.has_effect(amp_key)

    # Amp resolved last turn — clear everything and start the silent phase.
    if amp_active and len(active_desires) == 6:
        for k in (*_LUC_DUC_DESIRE_KEYS, amp_key):
            combatant.effects.pop(k, None)
            combatant.effect_overrides.pop(k, None)
        combatant.luc_duc_expire_turns_left = 2
        ctx.log.append(
            f"  💤 **{combatant.name}** Lục Dục tan biến — yên lặng 2 lượt."
        )
        return

    # Six desires reached — apply the amp marker; cycle settles next tick.
    if len(active_desires) == 6 and not amp_active:
        combatant.apply_effect(amp_key, 99)
        ctx.log.append(
            f"  🌌 **{combatant.name}** Lục Dục Cộng Minh — toàn bộ Lục Dục khuếch đại 20%."
        )
        return

    # Gain phase — stamp the next 2 desires that aren't already active.
    not_active = [
        k for k in _LUC_DUC_DESIRE_KEYS if k not in active_desires
    ]
    applied: list[str] = []
    for k in not_active[:2]:
        combatant.apply_effect(k, 99)
        meta = EFFECTS.get(k)
        applied.append(meta.vi if meta else k)
    if applied:
        ctx.log.append(
            f"  🌒 **{combatant.name}** Lục Dục Thiên Ma Vũ — kích hoạt: {', '.join(applied)}"
        )
