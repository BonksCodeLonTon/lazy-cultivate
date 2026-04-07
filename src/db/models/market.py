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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # grade: 1=Hoàng, 2=Huyền, 3=Địa, 4=Thiên
    grade: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Seller's asking price (in currency_type units)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    # Reference shop price used to compute 10% fee
    shop_ref_price: Mapped[int] = mapped_column(Integer, nullable=False)
    # "merit" | "primordial_stones"
    currency_type: Mapped[str] = mapped_column(String(32), nullable=False)

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
            f"<MarketListing id={self.id} seller={self.seller_id} "
            f"{self.item_key}×{self.quantity} price={self.price} {self.currency_type}>"
        )


from src.db.models.player import Player  # noqa: E402
