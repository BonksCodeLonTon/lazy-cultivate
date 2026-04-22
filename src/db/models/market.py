"""P2P market listing ORM model."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.models.base import Base, TimestampMixin


class MarketListing(Base, TimestampMixin):
    __tablename__ = "market_listings"
    __table_args__ = (
        Index("ix_market_seller_id", "seller_id"),
        Index("ix_market_grade", "grade"),
        Index("ix_market_item_key", "item_key"),
        Index("ix_market_expires_at", "expires_at"),
        Index("ix_market_listing_type", "listing_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )

    # "inventory" or "equipment"
    listing_type: Mapped[str] = mapped_column(String(16), nullable=False, default="inventory")

    # For inventory listings: item key + grade + quantity
    item_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grade: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # For equipment listings: FK to the ItemInstance (locked in "market" location)
    instance_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("item_instances.id", ondelete="SET NULL"), nullable=True
    )

    # Seller's asking price (always in merit)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    # Reference shop price for fee computation
    shop_ref_price: Mapped[int] = mapped_column(Integer, nullable=False)
    # Always "merit" now; kept for backward compat
    currency_type: Mapped[str] = mapped_column(String(32), nullable=False, default="merit")

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    seller: Mapped["Player"] = relationship("Player", back_populates="market_listings")

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def buyer_total(self) -> int:
        """Price + 10% fee on shop_ref_price × quantity."""
        fee = int(self.shop_ref_price * 0.10 * self.quantity)
        return self.price + fee

    def __repr__(self) -> str:
        return (
            f"<MarketListing id={self.id} type={self.listing_type} "
            f"seller={self.seller_id} price={self.price}>"
        )


from src.db.models.player import Player  # noqa: E402
