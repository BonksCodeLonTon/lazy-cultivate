"""Character skill slots ORM model."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base

MAX_SKILL_SLOTS = 6


class CharacterSkill(Base):
    __tablename__ = "character_skills"
    __table_args__ = (
        UniqueConstraint("player_id", "slot_index", name="uq_skill_player_slot"),
        Index("ix_char_skills_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False)
    slot_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0–5

    player: Mapped["Player"] = relationship("Player", back_populates="skills")

    def __repr__(self) -> str:
        return f"<CharacterSkill player={self.player_id} slot={self.slot_index} {self.skill_key}>"


from src.db.models.player import Player  # noqa: E402
