"""Character formation ORM model — 81-slot gem inlay per formation."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin

FORMATION_GEM_SLOTS = 10

# Mastery levels
MASTERY_LEVELS = ("chan_nhan", "chan_quan", "tien_ton", "dao_to")


class CharacterFormation(Base, TimestampMixin):
    """One row per (player, formation_key). Progress is preserved when switching."""

    __tablename__ = "character_formations"
    __table_args__ = (
        UniqueConstraint("player_id", "formation_key", name="uq_formation_player_key"),
        Index("ix_char_formations_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    formation_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # Realm progress within the formation (0-based)
    formation_realm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    formation_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Mastery: null | chan_nhan | chan_quan | tien_ton | dao_to
    mastery: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # 81-slot gem inlay — stored as JSONB dict {slot_index: gem_key}
    # e.g. {"0": "GemKim", "5": "GemHoa", ...}
    gem_slots: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Whether this is the currently active formation (only one per player)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    player: Mapped["Player"] = relationship("Player", back_populates="formations")

    def filled_slots(self) -> int:
        return len(self.gem_slots)

    def mastery_bonus_active(self) -> bool:
        """Đạo Tổ: treats all slots as inlaid + passive ×1.5."""
        return self.mastery == "dao_to"

    def __repr__(self) -> str:
        return (
            f"<CharacterFormation player={self.player_id} "
            f"{self.formation_key} mastery={self.mastery} "
            f"slots={self.filled_slots()}/{FORMATION_GEM_SLOTS}>"
        )


from src.db.models.player import Player  # noqa: E402
