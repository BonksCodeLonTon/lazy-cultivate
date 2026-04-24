"""Player ORM model — core identity + cultivation state."""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin


class Player(Base, TimestampMixin):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── Cultivation realms (0-based index, 1-based level) ──────────────────
    body_realm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    body_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    qi_realm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qi_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    formation_realm: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    formation_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # ── Constitution ───────────────────────────────────────────────────────
    constitution_type: Mapped[str] = mapped_column(
        String(64), default="ConstitutionVanTuong", nullable=False
    )
    dao_ti_unlocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Currencies ─────────────────────────────────────────────────────────
    merit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    karma_accum: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    karma_usable: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    primordial_stones: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Cultivation XP & active axis ──────────────────────────────────────
    active_axis: Mapped[str] = mapped_column(String(16), default="qi", nullable=False)
    body_xp: Mapped[int]      = mapped_column(Integer, default=0, nullable=False)
    qi_xp: Mapped[int]        = mapped_column(Integer, default=0, nullable=False)
    formation_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Combat session state (hp/mp persist across sessions) ───────────────
    hp_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mp_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Active formation ───────────────────────────────────────────────────
    active_formation: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── Linh Căn (Spiritual Roots) — comma-separated keys e.g. "kim,hoa" ──────
    linh_can: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    # ── Luyện Đan (alchemy) — accumulated pill toxicity ("Đan Độc") ──────────
    dan_doc: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Titles ─────────────────────────────────────────────────────────────
    main_title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sub_title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evil_title: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    turn_tracker: Mapped[TurnTracker] = relationship(
        "TurnTracker", back_populates="player", uselist=False, cascade="all, delete-orphan"
    )
    inventory: Mapped[list[InventoryItem]] = relationship(
        "InventoryItem", back_populates="player", cascade="all, delete-orphan"
    )
    skills: Mapped[list[CharacterSkill]] = relationship(
        "CharacterSkill", back_populates="player", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[CharacterArtifact]] = relationship(
        "CharacterArtifact", back_populates="player", cascade="all, delete-orphan"
    )
    formations: Mapped[list[CharacterFormation]] = relationship(
        "CharacterFormation", back_populates="player", cascade="all, delete-orphan"
    )
    market_listings: Mapped[list[MarketListing]] = relationship(
        "MarketListing", back_populates="seller", cascade="all, delete-orphan"
    )
    item_instances: Mapped[list[ItemInstance]] = relationship(
        "ItemInstance", back_populates="player", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Player id={self.id} discord_id={self.discord_id} name={self.name!r}>"


# Avoid circular import — import at module level after all models are defined
from src.db.models.turn_tracker import TurnTracker  # noqa: E402
from src.db.models.inventory import InventoryItem  # noqa: E402
from src.db.models.skill import CharacterSkill  # noqa: E402
from src.db.models.artifact import CharacterArtifact  # noqa: E402
from src.db.models.formation import CharacterFormation  # noqa: E402
from src.db.models.market import MarketListing  # noqa: E402
from src.db.models.item_instance import ItemInstance  # noqa: E402
