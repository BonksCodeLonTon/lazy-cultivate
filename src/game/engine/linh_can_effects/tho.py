"""Thổ Linh Căn — Hộ Thể (periodic shield).

Once per combat: when HP drops to ≤35%, grants a shield equal to 20% of max HP.
Phase: periodic (called at end of each turn).
"""
from __future__ import annotations

from src.game.systems.combatant import Combatant

_HP_THRESHOLD = 0.35
_SHIELD_PCT = 0.20


def check_shield(combatant: Combatant, log: list[str]) -> None:
    """Activate Hộ Thể shield if threshold is met and not yet used."""
    if "tho" not in combatant.linh_can:
        return
    if combatant.ho_the_used or not combatant.is_alive():
        return
    if combatant.hp <= int(combatant.hp_max * _HP_THRESHOLD):
        shield = int(combatant.hp_max * _SHIELD_PCT)
        combatant.shield = shield
        combatant.ho_the_used = True
        log.append(f"  🪨 **{combatant.name}** [Thổ] Hộ Thể kích hoạt! +{shield:,} khiên")
