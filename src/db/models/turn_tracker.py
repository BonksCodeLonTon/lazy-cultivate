"""Turn tracker ORM model — daily turn state per player."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base
from src.game.constants.currencies import BONUS_TURNS


class TurnTracker(Base):
    __tablename__ = "turn_trackers"

    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    turns_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bonus_turns_remaining: Mapped[int] = mapped_column(
        Integer, default=BONUS_TURNS, nullable=False
    )
    last_tick_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the merit×2 bonus from Thiên Đạo Phù Nghịch expires (nullable)
    merit_bonus_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    player: Mapped["Player"] = relationship("Player", back_populates="turn_tracker")

    def __repr__(self) -> str:
        return (
            f"<TurnTracker player_id={self.player_id} "
            f"turns_today={self.turns_today} bonus_left={self.bonus_turns_remaining}>"
        )


from src.db.models.player import Player  # noqa: E402
