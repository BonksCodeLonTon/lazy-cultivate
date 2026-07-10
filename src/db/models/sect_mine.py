"""Đại Chiến Khoáng Mạch ORM models — mine occupancy, wars, war attempts.

Mine rows are lazily seeded from ``src/data/sects/mines.json`` (one row per
``mine_key``); occupancy survives sect-side churn via ``ondelete="SET NULL"``
so a disbanded occupier simply frees the mine.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin


class SectMine(Base, TimestampMixin):
    __tablename__ = "sect_mines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mine_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)

    occupier_sect_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="SET NULL"), nullable=True
    )
    occupied_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Hourly payout marker — advanced by whole hours only (see
    # ``sect_mine.payout_hours``); NULL while unoccupied.
    last_payout_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Post-defense peace window — no new war may be declared before this.
    shield_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<SectMine {self.mine_key} occupier={self.occupier_sect_id}>"


class MineWar(Base, TimestampMixin):
    """One declared war over one mine. ``defender_sect_id`` NULL = PvE siege
    against the NPC garrison (mine was unoccupied at declaration)."""

    __tablename__ = "mine_wars"
    __table_args__ = (
        Index("ix_mine_wars_status", "status"),
        Index("ix_mine_wars_mine_status", "mine_key", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mine_key: Mapped[str] = mapped_column(String(32), nullable=False)

    attacker_sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    defender_sect_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=True
    )

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attacker_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    defender_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Siege-only shared HP pool (NULL for PvP wars).
    garrison_hp_max: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    garrison_hp_current: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    winner_sect_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    attacks: Mapped[list["MineWarAttack"]] = relationship(
        "MineWarAttack", back_populates="war", cascade="all, delete-orphan"
    )


class MineWarAttack(Base, TimestampMixin):
    """Per-player attempt/point tally for one war — both sides share the
    table, distinguished by ``side`` ("attack" / "defend")."""

    __tablename__ = "mine_war_attacks"
    __table_args__ = (
        UniqueConstraint("war_id", "player_id", name="uq_mine_war_attack"),
        Index("ix_mine_war_attacks_war", "war_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    war_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mine_wars.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    attempts_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    war: Mapped[MineWar] = relationship("MineWar", back_populates="attacks")
