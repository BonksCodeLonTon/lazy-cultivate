"""Tông Môn ORM models — sect core, membership, facilities, applications, logs, cooldowns.

Membership is a row in ``sect_members`` with a UNIQUE ``player_id`` — the
``players`` table carries no sect column (docs/tong_mon_design.md D5). Absence
of a member row = sect-less, so every non-sect code path stays query-free.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin


class Sect(Base, TimestampMixin):
    __tablename__ = "sects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Uniqueness is case-insensitive via the functional index declared below
    # (``uq_sects_name_lower``) — the column itself is not UNIQUE.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    tag: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)

    leader_player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id"), nullable=False
    )

    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    exp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    funds: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    announcement: Mapped[str | None] = mapped_column(String(500), nullable=True)
    emblem: Mapped[str | None] = mapped_column(String(16), nullable=True)

    members: Mapped[list["SectMember"]] = relationship(
        "SectMember", back_populates="sect", cascade="all, delete-orphan"
    )
    facilities: Mapped[list["SectFacility"]] = relationship(
        "SectFacility", back_populates="sect", cascade="all, delete-orphan"
    )
    applications: Mapped[list["SectApplication"]] = relationship(
        "SectApplication", back_populates="sect", cascade="all, delete-orphan"
    )
    logs: Mapped[list["SectLog"]] = relationship(
        "SectLog", back_populates="sect", cascade="all, delete-orphan"
    )
    storage_items: Mapped[list["SectStorageItem"]] = relationship(
        "SectStorageItem", back_populates="sect", cascade="all, delete-orphan"
    )
    storage_requests: Mapped[list["SectStorageRequest"]] = relationship(
        "SectStorageRequest", back_populates="sect", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Sect id={self.id} name={self.name!r} tag={self.tag!r} level={self.level}>"


# Case-insensitive unique name (Postgres functional index). Declared after the
# class so ``Sect.name`` resolves; registered on Base.metadata for create_all.
Index("uq_sects_name_lower", func.lower(Sect.name), unique=True)


class SectMember(Base, TimestampMixin):
    """One row per player in a sect — rank + contribution + daily counters.

    ``created_at`` doubles as the join timestamp.
    """

    __tablename__ = "sect_members"
    __table_args__ = (
        UniqueConstraint("player_id", name="uq_sect_members_player"),
        Index("ix_sect_members_sect", "sect_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )

    rank: Mapped[str] = mapped_column(String(16), default="de_tu", nullable=False)

    # Cống Hiến — spendable pool + lifetime tally (both wiped with the row on leave).
    contribution_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contribution_total: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Daily donation window — lazily reset by comparing ``donation_date`` to
    # today's UTC date (same convention as tick.py's day rollover).
    donated_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    donation_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Phase 4 seams — check-in + mission tracking (unused in Phase 1).
    last_checkin_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    mission_progress: Mapped[str] = mapped_column(String(512), default="{}", nullable=False)

    sect: Mapped[Sect] = relationship("Sect", back_populates="members")

    def __repr__(self) -> str:
        return f"<SectMember sect={self.sect_id} player={self.player_id} rank={self.rank}>"


class SectFacility(Base, TimestampMixin):
    """Per-sect facility level (Phase 2 upgrades — table ships now so the
    Phase 1 migration covers the whole core schema)."""

    __tablename__ = "sect_facilities"
    __table_args__ = (
        UniqueConstraint("sect_id", "facility_key", name="uq_sect_facility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    facility_key: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    sect: Mapped[Sect] = relationship("Sect", back_populates="facilities")


class SectApplication(Base, TimestampMixin):
    __tablename__ = "sect_applications"
    __table_args__ = (
        UniqueConstraint("sect_id", "player_id", name="uq_sect_application"),
        Index("ix_sect_applications_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    message: Mapped[str | None] = mapped_column(String(200), nullable=True)

    sect: Mapped[Sect] = relationship("Sect", back_populates="applications")


class SectLog(Base, TimestampMixin):
    """Append-only audit trail — pruned to the newest ``sect.SECT_LOG_KEEP``
    rows per sect on insert. ``actor_player_id`` is a soft reference (no FK)
    so log lines survive account deletion."""

    __tablename__ = "sect_logs"
    __table_args__ = (
        Index("ix_sect_logs_sect_created", "sect_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    actor_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(String(200), default="", nullable=False)

    sect: Mapped[Sect] = relationship("Sect", back_populates="logs")


class SectCooldown(Base):
    """Rejoin embargo after leaving / being kicked — one row per player,
    upserted each time. Checked before create/apply/join flows."""

    __tablename__ = "sect_cooldowns"

    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    rejoin_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SectStorageItem(Base, TimestampMixin):
    """Kho Tàng stock — one slot per (item_key, grade) stack. ``grade`` is the
    raw inventory grade int (1–4 qualities; legacy material rows go to 6), NOT
    the ``Grade`` enum, so historical grades round-trip losslessly."""

    __tablename__ = "sect_storage_items"
    __table_args__ = (
        UniqueConstraint("sect_id", "item_key", "grade", name="uq_sect_storage_slot"),
        Index("ix_sect_storage_sect", "sect_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    sect: Mapped[Sect] = relationship("Sect", back_populates="storage_items")

    def __repr__(self) -> str:
        return f"<SectStorageItem sect={self.sect_id} {self.item_key}×{self.quantity} g={self.grade}>"


class SectBossInstance(Base, TimestampMixin):
    """One Trấn Sơn Thú spawn per (sect, ISO week) — lazily created on the
    first ``/tongmon boss`` open of the week. Structural clone of
    ``WorldBossInstance`` + the sect/week scoping."""

    __tablename__ = "sect_boss_instances"
    __table_args__ = (
        UniqueConstraint("sect_id", "week_key", name="uq_sect_boss_week"),
        Index("ix_sect_boss_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    boss_key: Mapped[str] = mapped_column(String(64), nullable=False)
    week_key: Mapped[str] = mapped_column(String(12), nullable=False)

    hp_max: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hp_current: Mapped[int] = mapped_column(BigInteger, nullable=False)

    spawned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    killed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    finisher_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rewards_distributed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    participations: Mapped[list["SectBossParticipation"]] = relationship(
        "SectBossParticipation", back_populates="boss_instance",
        cascade="all, delete-orphan",
    )


class SectBossParticipation(Base, TimestampMixin):
    """Damage tally + claim flag for one player vs one sect-boss instance."""

    __tablename__ = "sect_boss_participations"
    __table_args__ = (
        UniqueConstraint("boss_instance_id", "player_id", name="uq_sect_boss_part"),
        Index("ix_sect_boss_part_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    boss_instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sect_boss_instances.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    damage_dealt: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    attack_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reward_claimed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    boss_instance: Mapped[SectBossInstance] = relationship(
        "SectBossInstance", back_populates="participations"
    )


class SectShopPurchase(Base, TimestampMixin):
    """Weekly-limit ledger for sect-shop purchases — one row per
    (member, item, grade, ISO week), quantity accumulated. Only written for
    slots that actually carry a ``weekly_limit``."""

    __tablename__ = "sect_shop_purchases"
    __table_args__ = (
        UniqueConstraint(
            "sect_id", "player_id", "item_key", "grade", "week_key",
            name="uq_sect_shop_purchase",
        ),
        Index("ix_sect_shop_purchases_player", "player_id", "week_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    week_key: Mapped[str] = mapped_column(String(12), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SectStorageRequest(Base, TimestampMixin):
    """Withdrawal request — pending until an officer resolves it (or it lazily
    expires). Resolved rows are kept as the audit trail; only PENDING rows are
    deleted when the requester leaves the sect."""

    __tablename__ = "sect_storage_requests"
    __table_args__ = (
        Index("ix_sect_storage_req_sect_status", "sect_id", "status"),
        Index("ix_sect_storage_req_player", "requester_player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sect_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sects.id", ondelete="CASCADE"), nullable=False
    )
    requester_player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reviewed_by_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sect: Mapped[Sect] = relationship("Sect", back_populates="storage_requests")
