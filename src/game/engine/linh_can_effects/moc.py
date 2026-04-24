"""Mộc Linh Căn — Hồi Xuân (passive HP regen).

Unlike the other modules, this isn't a combat proc: it's a flat passive
bonus folded into the stat pipeline, so the regen reaches the combatant
through the generic ``hp_regen_pct`` path already handled at end-of-turn.
Phase: stat-bonus (passive).
"""
from __future__ import annotations

from collections.abc import Iterable

HOI_XUAN_PCT = 0.04


def get_regen_bonus(linh_can: Iterable[str]) -> float:
    """Return the Hồi Xuân max-HP regen fraction for the given Linh Căn list."""
    return HOI_XUAN_PCT if "moc" in linh_can else 0.0
