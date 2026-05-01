"""Mộc Linh Căn — Hồi Xuân (passive HP regen).

Unlike the other modules, this isn't a combat proc: it's a flat passive
bonus folded into the stat pipeline, so the regen reaches the combatant
through the generic ``hp_regen_pct`` path already handled at end-of-turn.
Phase: stat-bonus (passive).
"""
from __future__ import annotations

from collections.abc import Iterable

HOI_XUAN_PCT = 0.04
HOI_XUAN_PER_LEVEL = 0.005   # +0.5% HP/turn per level above 1


def get_regen_bonus(linh_can: Iterable[str], level: int = 1) -> float:
    """Return the Hồi Xuân max-HP regen fraction, scaled by Mộc level."""
    if "moc" not in linh_can:
        return 0.0
    return HOI_XUAN_PCT + max(0, level - 1) * HOI_XUAN_PER_LEVEL
