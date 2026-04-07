"""Character artifact slots ORM model (3 slots: sword / armor / artifact)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base

ARTIFACT_SLOTS = ("sword", "armor", "artifact")


class CharacterArtifact(Base):
    __tablename__ = "character_artifacts"
    __table_args__ = (
        UniqueConstraint("player_id", "slot", name="uq_artifact_player_slot"),
        Index("ix_char_artifacts_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    # slot: "sword" | "armor" | "artifact"
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(64), nullable=False)

    player: Mapped["Player"] = relationship("Player", back_populates="artifacts")

    def __repr__(self) -> str:
        return f"<CharacterArtifact player={self.player_id} [{self.slot}]={self.artifact_key}>"


from src.db.models.player import Player  # noqa: E402
