"""Thổ Linh Căn — Hộ Thể (periodic shield).

Once per combat: when HP drops to ≤35%, grants a shield. Base shield is
20% of max HP and scales with Thổ Linh Căn level (every level adds +2%).
Phase: periodic (called at end of each turn).
"""
from __future__ import annotations

from src.game.systems.combatant import Combatant

_HP_THRESHOLD = 0.35
_BASE_SHIELD_PCT = 0.20
_SHIELD_PER_LEVEL = 0.02


def check_shield(combatant: Combatant, log: list[str]) -> None:
    """Activate Hộ Thể shield if threshold is met and not yet used."""
    if "tho" not in combatant.linh_can:
        return
    if combatant.ho_the_used or not combatant.is_alive():
        return
    if combatant.hp <= int(combatant.hp_max * _HP_THRESHOLD):
        level = combatant.linh_can_levels.get("tho", 1) if combatant.linh_can_levels else 1
        shield_pct = _BASE_SHIELD_PCT + max(0, level - 1) * _SHIELD_PER_LEVEL
        shield = int(combatant.hp_max * shield_pct)
        combatant.shield = shield
        combatant.ho_the_used = True
        log.append(
            f"  🪨 **{combatant.name}** [Thổ Lv{level}] Hộ Thể kích hoạt! "
            f"+{shield:,} khiên"
        )
