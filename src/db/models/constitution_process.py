"""Per-constitution progression ORM model (Constitution Process feature).

One row per (player, constitution) tracks a body's progression so it SURVIVES
body-swaps: swapping away and back later resumes the old body's level. Mirrors
``CharacterSkillMastery`` but is keyed by ``constitution_key`` and has no
``hidden_unlocked`` column — constitutions have no hidden band.
"""
from __future__ import annotations

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base


class CharacterConstitutionProgress(Base):
    __tablename__ = "character_constitution_progress"
    __table_args__ = (
        UniqueConstraint(
            "player_id", "constitution_key", name="uq_cons_proc_player_key"
        ),
        Index("ix_cons_proc_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    constitution_key: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gate_fails: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    breakthrough_cooldown_until: Mapped[int | None] = mapped_column(
        Integer, default=None, nullable=True
    )

    player: Mapped["Player"] = relationship(
        "Player", back_populates="constitution_progress"
    )

    def __repr__(self) -> str:
        return (
            f"<CharacterConstitutionProgress player={self.player_id} "
            f"{self.constitution_key} lv={self.level} xp={self.xp}>"
        )


from src.db.models.player import Player  # noqa: E402
