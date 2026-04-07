"""Quang Linh Căn — Thanh Tẩy (pre-turn cleanse).

15% chance to remove 1 debuff/CC from self and restore 5% MP.
Phase: pre_turn (called on the *actor* before they act).
"""
from __future__ import annotations

import random

from src.game.systems.combatant import Combatant


def try_cleanse(actor: Combatant, rng: random.Random, log: list[str]) -> None:
    """Attempt to cleanse one debuff and restore MP. No return value."""
    if "quang" not in actor.linh_can:
        return
    if rng.random() >= 0.15:
        return
    debuffs = [k for k in list(actor.effects) if "Debuff" in k or "CC" in k]
    if not debuffs:
        return
    removed = debuffs[0]
    del actor.effects[removed]
    mp_restore = int(actor.mp_max * 0.05 * (1.0 + actor.heal_pct))
    actor.mp = min(actor.mp_max, actor.mp + mp_restore)
    log.append(f"  ✨ **{actor.name}** [Quang] Thanh Tẩy: giải *{removed}* | +{mp_restore} MP")
