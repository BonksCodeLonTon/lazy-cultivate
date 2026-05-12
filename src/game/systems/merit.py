"""Merit (Công Đức) buff helpers — single source of truth for the
``BuffMeritX2_30days`` (Thiên Đạo Phù Nghịch) ×2 multiplier.

Two pieces:

* ``merit_multiplier(player)`` reads ``player.turn_tracker.merit_bonus_expires_at``
  and returns 2.0 when the buff is active, else 1.0.
* ``grant_merit(player, amount)`` adds ``amount × multiplier`` to
  ``player.merit`` (clamped to ``CURRENCY_CAP``) and returns
  ``(actual_added, multiplier)`` so callers can render an x2 indicator.

Every site that **earns** merit (cultivation tick, dungeon clear, combat
victory, pill consumption) should route through ``grant_merit``.
Transactional flows (market sale price, refund) intentionally stay raw
— they're not "earned" income.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.game.constants.currencies import CURRENCY_CAP


def merit_multiplier(player) -> float:
    """Return 2.0 if Thiên Đạo Phù Nghịch is active for ``player``, else 1.0.

    Tolerates a missing ``turn_tracker`` (returns 1.0) so callers don't
    have to null-check before computing rewards.
    """
    tt = getattr(player, "turn_tracker", None)
    if tt is None:
        return 1.0
    expires = getattr(tt, "merit_bonus_expires_at", None)
    if expires is None:
        return 1.0
    if expires > datetime.now(timezone.utc):
        return 2.0
    return 1.0


def grant_merit(player, amount: int) -> tuple[int, float]:
    """Add ``amount × merit_multiplier(player)`` to ``player.merit``.

    Clamps to ``CURRENCY_CAP`` so the merit pool can't overflow. Returns
    ``(actual_added, multiplier)`` — ``actual_added`` is what landed
    after the cap (may be less than the multiplied amount when the
    player is already near the cap).
    """
    if amount <= 0:
        return 0, 1.0
    mult = merit_multiplier(player)
    final = int(amount * mult)
    new_total = min(int(player.merit or 0) + final, CURRENCY_CAP)
    actual = new_total - int(player.merit or 0)
    player.merit = new_total
    return actual, mult
