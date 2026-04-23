"""World boss ORM models — persistent boss instances and per-player participation."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin


class WorldBossInstance(Base, TimestampMixin):
    """A single active (or completed) world boss spawn.

    Only one *active* instance per boss_key may exist at a time; once the
    boss dies or its spawn window ends, the row is marked ``is_active=False``
    and kept for reward distribution / history.
    """

    __tablename__ = "world_boss_instances"
    __table_args__ = (
        Index("ix_world_boss_active", "is_active"),
        Index("ix_world_boss_key_active", "boss_key", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    boss_key: Mapped[str] = mapped_column(String(64), nullable=False)
    realm: Mapped[int] = mapped_column(Integer, nullable=False)

    hp_max: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hp_current: Mapped[int] = mapped_column(BigInteger, nullable=False)

    spawned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    killed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    finisher_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rewards_distributed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    participations: Mapped[list["WorldBossParticipation"]] = relationship(
        "WorldBossParticipation", back_populates="boss_instance", cascade="all, delete-orphan"
    )


class WorldBossParticipation(Base, TimestampMixin):
    """Damage tally + reward-claimed flag for one player against one boss instance."""

    __tablename__ = "world_boss_participations"
    __table_args__ = (
        UniqueConstraint("boss_instance_id", "player_id", name="uq_wb_part_boss_player"),
        Index("ix_wb_part_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    boss_instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("world_boss_instances.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    damage_dealt: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    attack_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reward_claimed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    boss_instance: Mapped[WorldBossInstance] = relationship(
        "WorldBossInstance", back_populates="participations"
    )
