"""Item instance ORM — each row is one physical item owned by a player."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin

EQUIPMENT_SLOTS = ("weapon", "off_hand", "armor", "helmet", "glove", "belt", "boot", "ring", "amulet")


class ItemInstance(Base, TimestampMixin):
    """One physical item owned by a player.

    - location = "bag"      → item is in the player's bag (not worn)
    - location = "equipped" → item is worn

    `slot` always holds the item's equipment slot type (e.g. "weapon", "armor").
    It is set at generation time and never changed.

    base_key + affixes define a normal generated item.
    unique_key defines a unique item (affixes will be empty).
    computed_stats is a pre-summed dict of all stats for fast lookup.
    """

    __tablename__ = "item_instances"
    __table_args__ = (
        Index("ix_item_inst_player_id", "player_id"),
        Index("ix_item_inst_location", "player_id", "location"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    location: Mapped[str] = mapped_column(String(16), nullable=False, default="bag")
    # Equipment slot type — always set (e.g. "weapon", "armor"). Never None.
    slot: Mapped[str] = mapped_column(String(16), nullable=False)

    base_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unique_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # [{key, stat, value, type}]
    affixes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # {stat_key: float} — implicit_stats + all affix values summed
    computed_stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    grade: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    player: Mapped["Player"] = relationship("Player", back_populates="item_instances")

    def __repr__(self) -> str:
        return (
            f"<ItemInstance id={self.id} player={self.player_id} "
            f"loc={self.location} slot={self.slot} name={self.display_name!r}>"
        )


from src.db.models.player import Player  # noqa: E402
