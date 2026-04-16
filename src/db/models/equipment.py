"""Player equipment ORM model — one row per equipped slot per player."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin

EQUIPMENT_SLOTS = ("weapon", "off_hand", "armor", "helmet", "glove", "belt", "ring", "amulet")


class PlayerEquipment(Base, TimestampMixin):
    """One row per (player, slot). Inserting a new row for an occupied slot must
    first delete the old row (handled by the repository)."""

    __tablename__ = "player_equipment"
    __table_args__ = (
        UniqueConstraint("player_id", "slot", name="uq_equipment_player_slot"),
        Index("ix_player_equipment_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    player: Mapped["Player"] = relationship("Player", back_populates="equipment")

    def __repr__(self) -> str:
        return f"<PlayerEquipment player={self.player_id} slot={self.slot} item={self.item_key} g={self.grade}>"


from src.db.models.player import Player  # noqa: E402
