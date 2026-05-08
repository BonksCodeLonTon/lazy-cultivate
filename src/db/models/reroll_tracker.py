"""Reroll tracker ORM model — counts /reset_character uses per Discord user.

Keyed on ``discord_id`` (not ``player_id``) so the counter survives the
player row being deleted by reset. A user cannot bypass the cap by
deleting their character and re-registering.

Also tracks the one-shot legendary constitution reroll
(``/legendary_reroll``): each Discord user gets exactly one for life.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, TimestampMixin


class RerollTracker(Base, TimestampMixin):
    __tablename__ = "reroll_trackers"

    discord_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    legendary_reroll_used: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    def __repr__(self) -> str:
        return (
            f"<RerollTracker discord_id={self.discord_id} count={self.count} "
            f"legendary_used={self.legendary_reroll_used}>"
        )
