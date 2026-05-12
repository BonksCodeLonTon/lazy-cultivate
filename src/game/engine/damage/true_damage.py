"""True damage (Sát Thương Chuẩn) — single application site.

The mechanic in one place. Every contributor to ``true_dmg_pct``
(constitution, unique weapon, unique gem, player skill, Linh Căn passive)
funnels into this one helper so the formula, cap, and log line stay in
sync across the codebase.

Formula
-------
    pct      = clamp(skill_pct + actor.true_dmg_pct, 0, TRUE_DMG_PCT_CAP)
    bonus    = max(1, int(base_damage * pct * TRUE_DMG_OUTPUT_MULT))
    if is_crit: bonus *= TRUE_DMG_CRIT_MULT
    target.take_damage(bonus)

``base_damage`` is the *post-mitigation* damage the attacker just landed
on the target — the true-damage scalar is applied on top of that, so a
stronger hit naturally produces a stronger true-damage chip. The
``TRUE_DMG_OUTPUT_MULT`` knob lets us tune the mechanic's overall
strength without touching per-source pct contributions or raising the
cap. The new model:

  * Scales with the attacker's offense, not the defender's HP pool.
  * Removes the need for a world-boss carve-out — true damage is
    bounded by ``base_damage``, which is already capped per-attack vs
    world bosses (see ``world_boss.PER_ATTACK_DMG_CAP_PCT``).
  * Keeps a crit multiplier (``TRUE_DMG_CRIT_MULT``) so crit-true-damage
    builds stay meaningful.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.balance import (
    TRUE_DMG_CRIT_MULT,
    TRUE_DMG_OUTPUT_MULT,
    TRUE_DMG_PCT_CAP,
)
from src.game.engine.damage.color import colorize_damage

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


def apply_true_damage(
    actor: "Combatant",
    target: "Combatant",
    base_damage: int,
    is_crit: bool,
    log: list[str],
    skill_pct: float = 0.0,
) -> int:
    """Apply Sát Thương Chuẩn on top of ``base_damage``. Returns damage applied.

    ``base_damage`` is the post-mitigation damage from the main hit; the
    true-damage scalar is applied to it (not to target hp_max). Returns
    0 when no true damage was dealt (target dead, pct == 0, etc.) so
    callers can no-op cleanly.
    """
    if not target.is_alive() or base_damage <= 0:
        return 0

    pct = max(0.0, min(TRUE_DMG_PCT_CAP, skill_pct + actor.true_dmg_pct))
    if pct <= 0:
        return 0

    bonus = max(1, int(base_damage * pct * TRUE_DMG_OUTPUT_MULT))
    if is_crit:
        bonus = int(bonus * TRUE_DMG_CRIT_MULT)

    target.take_damage(bonus)
    tag = colorize_damage(f"-{bonus:,} HP", None, true_dmg=True)
    log.append(
        f"    🗡️ **Sát Thương Chuẩn** xuyên mọi phòng ngự → {tag} "
        f"(+{pct * 100:.1f}% sát thương)"
    )
    return bonus
