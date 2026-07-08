"""Hóa Hình — the 9/9 one-bloodline beast transformation (Bách Thể Chú Linh).

A Thể Tu whose nine body parts all carry the SAME vital essence unlocks the
form (``body_parts.hoa_hinh_form`` → build-time config on the Combatant).
Once per battle, at the start of a round in which the holder sits below
``HOA_HINH_TRIGGER_HP_PCT`` of max HP, the bloodline erupts:

  * restores ``HOA_HINH_HEAL_PCT`` of max HP (through ``_apply_heal`` so
    heal crits / reductions / overheal conversion apply normally), then
  * stamps the archetype's form buff (``BuffHoaHinh*`` in effects/buffs.json)
    for its authored duration — the payload follows the essence archetype,
    the log announces the specific beast.

The latch (``hoa_hinh_used``) never resets within a battle, mirroring
``phoenix_revive_used``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.systems.body_parts import (
    HOA_HINH_HEAL_PCT,
    HOA_HINH_TRIGGER_HP_PCT,
)
from src.game.systems.combatant import Combatant

from .context import TurnContext
from .hooks import TurnPhase, register_hook

if TYPE_CHECKING:
    from .session import CombatSession


def _transform(session: "CombatSession", combatant: Combatant) -> bool:
    """Shared transformation core: latch, announce, stamp the form buff.

    Returns False when the buff config is missing (never latches on bad
    data). The HEAL is applied by each trigger path — the proactive path
    heals through ``_apply_heal`` (reductions apply), the death-rescue path
    sets HP directly like the other revive hooks.
    """
    buff_key = combatant.hoa_hinh_buff_key
    from src.game.engine.effects import EFFECTS, default_duration
    meta = EFFECTS.get(buff_key)
    if meta is None:                       # defensive — bad config never latches
        return False
    combatant.hoa_hinh_used = True
    duration = default_duration(buff_key)
    beast = combatant.hoa_hinh_beast_vi or "Yêu Thú"
    session.log.append(
        f"  {meta.emoji or '🐲'} **{combatant.name}** huyết mạch bùng nổ — "
        f"**HÓA HÌNH {beast.upper()}**! *{meta.vi}* ({duration} lượt)"
    )
    combatant.apply_effect(buff_key, duration)
    return True


def try_hoa_hinh(session: "CombatSession", combatant: Combatant) -> None:
    """Proactive trigger: armed, alive, un-triggered, and bloodied."""
    if not combatant.hoa_hinh_buff_key or combatant.hoa_hinh_used:
        return
    if not combatant.is_alive() or combatant.hp_max <= 0:
        return
    if combatant.hp / combatant.hp_max >= HOA_HINH_TRIGGER_HP_PCT:
        return
    if not _transform(session, combatant):
        return
    healed = session._apply_heal(
        combatant, int(combatant.hp_max * HOA_HINH_HEAL_PCT),
    )
    if healed > 0:
        session.log.append(f"    💗 Thân thể tái tạo: +{healed:,} HP")


# ── Death-rescue trigger (ON_REVIVE hook) ────────────────────────────────────
# Endgame fights routinely go 100% → 0 inside a single phase, so the
# round-end proactive check alone almost never fires. The bloodline refuses
# to die quietly: a killing blow force-triggers the transformation instead
# (once per battle, shared latch with the proactive path). Priority 15 —
# dedicated revive passives (phoenix at 10) claim the death first, keeping
# Hóa Hình armed for a later death.


def _hoa_hinh_rescue_ready(ctx: TurnContext) -> bool:
    return (
        not ctx.scratch.get("revived")
        and not ctx.actor.is_alive()
        and bool(ctx.actor.hoa_hinh_buff_key)
        and not ctx.actor.hoa_hinh_used
    )


@register_hook(
    phase=TurnPhase.ON_REVIVE, name="hoa_hinh_rescue", priority=15,
    predicate=_hoa_hinh_rescue_ready,
)
def _hoa_hinh_rescue(ctx: TurnContext) -> bool | None:
    combatant = ctx.actor
    if not _transform(ctx.session, combatant):
        return None
    combatant.hp = max(1, int(combatant.hp_max * HOA_HINH_HEAL_PCT))
    ctx.session.log.append(
        f"    💗 Thú huyết nghịch chuyển sinh cơ: hồi sinh với "
        f"{combatant.hp:,} HP"
    )
    ctx.scratch["revived"] = True
    return True
