"""Quang Linh Căn — Thanh Tẩy (pre-turn cleanse).

Base: 15% chance to remove 1 debuff/CC from self and restore 5% MP.
Formation/equipment bonus ``cleanse_on_turn_pct`` stacks additively on top.
When ``barrier_on_cleanse`` is set, each successful cleanse also grants a
small MATK-scaled shield (distinct from Thổ Hộ Thể, which is one-shot).

Quang's cleanse also partially unwinds the Âm build's permanent mutations:
each successful cleanse pulse reclaims a slice of drained ``hp_max`` and
stolen atk/matk/def. The lost stats go back to the target only — the Âm
attacker keeps what they took (they earned it) but the defender can slowly
recover if they live long enough. This lets Quang actively counter Âm
without trivially negating it.

Phase: pre_turn (called on the *actor* before they act).
"""
from __future__ import annotations

import random

from src.game.constants.balance import (
    QUANG_CLEANSE_AM_HP_RESTORE_PCT,
    QUANG_CLEANSE_AM_STAT_RESTORE_PCT,
)
from src.game.systems.combatant import Combatant

_BASE_CLEANSE_CHANCE = 0.15
_BASE_MP_RESTORE_PCT = 0.05
_BARRIER_MATK_SCALE = 0.5


def _try_restore_am_mutations(actor: Combatant, log: list[str]) -> bool:
    """Undo a chunk of Âm soul-drain / stat-steal on the cleanser.

    Returns True if anything was restored so the caller can count the pulse
    as a successful cleanse (enabling MP restore + barrier even when the
    actor carries no debuff/CC entry).
    """
    did_restore = False
    restored_parts: list[str] = []

    # ── Reclaim drained hp_max ───────────────────────────────────────────
    if actor.hp_max_drained > 0 and actor.hp_max_original > 0:
        restore = min(
            actor.hp_max_drained,
            max(1, int(actor.hp_max_original * QUANG_CLEANSE_AM_HP_RESTORE_PCT)),
        )
        actor.hp_max_drained -= restore
        actor.hp_max += restore
        restored_parts.append(f"+{restore:,} HP Max")
        did_restore = True

    # ── Reclaim stolen atk / matk / def ──────────────────────────────────
    steal_axes = [
        ("atk", "stolen_atk", "atk_original", "Công"),
        ("matk", "stolen_matk", "matk_original", "Pháp"),
        ("def_stat", "stolen_def", "def_stat_original", "Phòng"),
    ]
    for live_attr, stolen_attr, snap_attr, label in steal_axes:
        stolen = getattr(actor, stolen_attr, 0)
        start = getattr(actor, snap_attr, 0)
        if stolen <= 0 or start <= 0:
            continue
        restore = min(stolen, max(1, int(start * QUANG_CLEANSE_AM_STAT_RESTORE_PCT)))
        setattr(actor, stolen_attr, stolen - restore)
        setattr(actor, live_attr, getattr(actor, live_attr) + restore)
        restored_parts.append(f"+{restore} {label}")
        did_restore = True

    if did_restore:
        log.append(
            f"    🕊️ Thánh Quang Hồi Nguyên — gột bỏ vết Ám: "
            f"{' / '.join(restored_parts)}"
        )
    return did_restore


def try_cleanse(actor: Combatant, rng: random.Random, log: list[str]) -> None:
    """Attempt to cleanse one debuff and restore MP. No return value.

    Quang cleanse is a three-step pulse:
      1) remove one Debuff/CC entry if present,
      2) reclaim a slice of Âm-build mutations (drained HP max / stolen
         stats) if present,
      3) if either of the above landed, restore MP and optionally raise
         a MATK-scaled barrier (``barrier_on_cleanse``).
    """
    if "quang" not in actor.linh_can:
        return
    chance = _BASE_CLEANSE_CHANCE + actor.cleanse_on_turn_pct
    if rng.random() >= chance:
        return

    did_cleanse = False

    debuffs = [k for k in list(actor.effects) if "Debuff" in k or "CC" in k]
    if debuffs:
        removed = debuffs[0]
        del actor.effects[removed]
        log.append(f"  ✨ **{actor.name}** [Quang] Thanh Tẩy: giải *{removed}*")
        did_cleanse = True

    if _try_restore_am_mutations(actor, log):
        did_cleanse = True

    if not did_cleanse:
        return

    mp_restore = int(actor.mp_max * _BASE_MP_RESTORE_PCT * (1.0 + actor.heal_pct))
    actor.mp = min(actor.mp_max, actor.mp + mp_restore)
    log.append(f"    💙 +{mp_restore} MP")

    if actor.barrier_on_cleanse and actor.is_alive():
        shield_amt = max(1, int(actor.matk * _BARRIER_MATK_SCALE * (1.0 + actor.heal_pct)))
        cap = actor.shield_cap()
        before = actor.shield
        actor.shield = min(cap, actor.shield + shield_amt)
        gained = actor.shield - before
        if gained > 0:
            log.append(f"    🛡️ Thánh Quang Hộ Thuẫn: +{gained:,} khiên")
