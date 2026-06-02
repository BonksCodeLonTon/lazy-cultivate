"""Per-skill mastery progression ORM model (Skill Mastery feature)."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base


class CharacterSkillMastery(Base):
    __tablename__ = "character_skill_mastery"
    __table_args__ = (
        UniqueConstraint("player_id", "skill_key", name="uq_mastery_player_skill"),
        Index("ix_char_skill_mastery_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gate_fails: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    hidden_unlocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    player: Mapped["Player"] = relationship("Player", back_populates="skill_masteries")

    def __repr__(self) -> str:
        return (
            f"<CharacterSkillMastery player={self.player_id} {self.skill_key} "
            f"lv={self.level} xp={self.xp}>"
        )


from src.db.models.player import Player  # noqa: E402
