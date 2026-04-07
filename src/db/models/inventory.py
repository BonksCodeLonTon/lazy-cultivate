"""Inventory ORM model."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin


class InventoryItem(Base, TimestampMixin):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("player_id", "item_key", "grade", name="uq_inventory_player_item_grade"),
        Index("ix_inventory_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # grade stored as int: 1=Hoàng, 2=Huyền, 3=Địa, 4=Thiên
    grade: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    player: Mapped["Player"] = relationship("Player", back_populates="inventory")

    def __repr__(self) -> str:
        return f"<InventoryItem player={self.player_id} {self.item_key}×{self.quantity} g={self.grade}>"


from src.db.models.player import Player  # noqa: E402
