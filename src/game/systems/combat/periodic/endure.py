"""Survival-related periodic ticks.

Three hooks live here because they all manage "stay-alive" state and
their order matters relative to the DoT loop:

  * ``endure_announcement`` (priority 10) — logs ``endure_just_triggered``
    and clears the flag. Runs first so the survival message shows up
    before any periodic damage in the same tick.
  * ``thanh_tuyen_deferred`` (priority 15) — pays out one queued
    installment of Thánh Tuyền deferred damage. Bypasses
    ``take_damage`` to avoid re-deferring the already-deferred chunk.
  * ``endure_cooldown`` (priority 70) — decrements ``endure_remaining``
    after all other periodic effects resolved, so the cooldown counter
    advances at end-of-round.
"""
from __future__ import annotations

from src.game.engine.damage.color import colorize_damage

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="endure_announcement", priority=10)
def _endure_announcement(ctx: TurnContext) -> None:
    combatant = ctx.actor
    if not combatant.endure_just_triggered:
        return
    ctx.log.append(
        f"  🌿 **{combatant.name}** **Cội Nguồn Bất Tận** — sống sót ở "
        f"{combatant.hp:,}/{combatant.hp_max:,} HP "
        f"(hồi chiêu {combatant.endure_remaining}t)"
    )
    combatant.endure_just_triggered = False


@register_hook(phase=TurnPhase.PERIODIC, name="aegis_reform_announcement", priority=11)
def _aegis_reform_announcement(ctx: TurnContext) -> None:
    """Lưu Ly Bất Diệt (L9) — announce the once-per-fight aegis reform.

    The reform itself fires silently inside ``take_damage`` (the model has no
    log handle); this clears the flag and shows the survival message, mirroring
    ``endure_announcement``.
    """
    combatant = ctx.actor
    if not combatant.aegis_reform_just_triggered:
        return
    ctx.log.append(
        f"  🔷 **{combatant.name}** **Lưu Ly Bất Diệt** — thuẫn lưu ly tái tạo, "
        f"vô hiệu đòn chí mạng (Khiên {combatant.shield:,})"
    )
    combatant.aegis_reform_just_triggered = False


@register_hook(phase=TurnPhase.PERIODIC, name="thanh_tuyen_deferred", priority=15)
def _thanh_tuyen_deferred(ctx: TurnContext) -> None:
    combatant = ctx.actor
    if not combatant.deferred_damage_queue:
        return
    installment = combatant.deferred_damage_queue.pop(0)
    if installment <= 0:
        return
    combatant.hp = max(0, combatant.hp - installment)
    ctx.log.append(
        f"  💧 **{combatant.name}** Thánh Tuyền hoàn trả "
        f"{colorize_damage(f'-{installment:,} HP', 'thuy')}"
    )


@register_hook(phase=TurnPhase.PERIODIC, name="endure_cooldown", priority=70)
def _endure_cooldown(ctx: TurnContext) -> None:
    combatant = ctx.actor
    if combatant.endure_remaining > 0:
        combatant.endure_remaining -= 1
