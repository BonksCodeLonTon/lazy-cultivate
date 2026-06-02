"""Đồng Quy Vu Tận — delayed HP-race detonation seal.

The seal (``DebuffDongQuyAn``) is a single-application debuff (not a stack
build). On apply it snapshots BOTH duellists' hp%-at-apply into the holder's
``effect_overrides`` so the detonation can later measure how much HP each side
has *lost* since the seal landed. When the seal expires *naturally* (duration
ticks to 0 — never on cleanse), it detonates: the more the holder has bled
relative to the applier, the bigger the blast.

Why drive it off the on-expire snapshot rather than a raw PERIODIC hook at
priority 82
--------------------------------------------------------------------------
The PERIODIC ``expiry`` hook (priority 80) calls ``tick_effects()``, which
pops the effect AND its override before any priority-82 hook could read the
snapshot. So a raw 82 hook would always see an empty override. Instead — exactly
like the aegis discharge-on-expire (``emit_discharge_on_expire``) — the expiry
hook snapshots overrides BEFORE ``tick_effects`` and calls
``emit_dong_quy_detonate`` per expired key with the intact snapshot. This
guarantees:
  * fires only on NATURAL expiry (cleansed effects never reach ``tick_effects``'
    expired list), matching the design ("Khi tan");
  * reads apply-time hp% even though the live override is already gone.

The applier reference is recovered at detonation time as the holder's opponent
(``opponent`` arg the expiry hook already threads through). We stored the
applier's apply-time hp% in the snapshot, and read the applier's *current* hp%
off that opponent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.game.constants.effects import EffectKey
from src.game.engine.damage.color import colorize_damage

from .stack_stampers import register_stack_stamper

if TYPE_CHECKING:
    from src.game.engine.effects import EffectMeta
    from src.game.systems.combatant import Combatant

    from .session import CombatSession


def _hp_pct(c: "Combatant") -> float:
    return c.hp / c.hp_max if c.hp_max > 0 else 0.0


@register_stack_stamper(EffectKey.DEBUFF_DONG_QUY_AN)
def _stamp_dong_quy_an(
    session: "CombatSession", actor: "Combatant | None",
    target: "Combatant", meta: "EffectMeta", dur: int,
    overrides: "dict | None",
) -> None:
    """Snapshot both sides' apply-time hp% into the holder's override.

    Registered as a "stamper" purely to get a post-``apply_effect`` hook that
    sees the applier (``actor``) and holder (``target``). Stores:
      * ``_dq_holder_hp_pct`` — holder hp% at apply (the enemy losing HP)
      * ``_dq_applier_hp_pct`` — applier hp% at apply (us, the pact-binder)
    Re-applying refreshes the snapshot to the latest apply moment.
    """
    ovr = target.effect_overrides.setdefault(EffectKey.DEBUFF_DONG_QUY_AN.value, {})
    ovr["_dq_holder_hp_pct"] = _hp_pct(target)
    if actor is not None:
        ovr["_dq_applier_hp_pct"] = _hp_pct(actor)
    session.log.append(
        f"    {meta.emoji} **{target.name}** bị **{meta.vi}** ({dur}t)"
    )


def emit_dong_quy_detonate(
    session: "CombatSession",
    holder: "Combatant",
    opponent: Optional["Combatant"],
    expired_key: str,
    overrides_snapshot: dict,
) -> None:
    """Detonate the seal on natural expiry, scaled by the HP-loss race.

    ``opponent`` is the holder's opponent — i.e. the applier (the duel only
    has two combatants). Reads ``expire_detonate_hp_race`` config + the
    apply-time hp% snapshots. Damage is a ``general`` true strike (bypasses
    shield) so the pact resolves regardless of defenses.

    enemy_lost = snapshot_holder_hp_pct − holder_now_hp_pct
    self_lost  = snapshot_applier_hp_pct − applier_now_hp_pct
    diff       = max(0, enemy_lost − self_lost)
    dmg        = min(cap_pct, diff × per_pct_diff) × holder.hp_max
    """
    if expired_key != EffectKey.DEBUFF_DONG_QUY_AN.value:
        return
    snap = overrides_snapshot.get(expired_key) or {}
    cfg = snap.get("expire_detonate_hp_race")
    if not isinstance(cfg, dict):
        return
    if not holder.is_alive():
        return

    per_pct = float(cfg.get("per_pct_diff_dmg_pct", 0.0))
    cap_pct = float(cfg.get("cap_pct_hp_max", 0.0))
    element = str(cfg.get("element", "general"))

    holder_at_apply = float(snap.get("_dq_holder_hp_pct", _hp_pct(holder)))
    enemy_lost = max(0.0, holder_at_apply - _hp_pct(holder))

    if opponent is not None and "_dq_applier_hp_pct" in snap:
        applier_at_apply = float(snap["_dq_applier_hp_pct"])
        self_lost = max(0.0, applier_at_apply - _hp_pct(opponent))
    else:
        self_lost = 0.0

    diff = max(0.0, enemy_lost - self_lost)
    if diff <= 0:
        session.log.append(
            f"  💀 **{holder.name}** **Đồng Quy Ấn** tan — không áp đảo, ấn tắt lịm."
        )
        return
    dmg_pct = min(cap_pct, diff * per_pct) if per_pct > 0 else 0.0
    det_dmg = max(1, int(dmg_pct * holder.hp_max))
    holder.take_damage(det_dmg, bypass_shield=True)
    tag = colorize_damage(f"-{det_dmg:,} HP", element)
    session.log.append(
        f"  💀 **Đồng Quy Ấn** bùng nổ trên **{holder.name}** — chênh lệch "
        f"{int(diff * 100)}% HP đã mất → {tag} "
        f"| {holder.hp:,}/{holder.hp_max:,} HP"
    )
