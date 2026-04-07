"""P2P trading marketplace system."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.game.constants.currencies import (
    MARKET_LISTING_HOURS,
    MARKET_MAX_LISTINGS,
    TRADE_FEE_RATE,
)
from src.game.constants.grades import Grade, GRADE_CURRENCY


@dataclass
class MarketListing:
    id: int
    seller_id: int
    item_key: str
    grade: Grade
    quantity: int
    price: int               # Price set by seller (in currency for grade)
    shop_ref_price: int      # Reference shop price (for fee calculation)
    currency_type: str       # "merit" or "primordial_stones"
    expires_at: datetime
    created_at: datetime


def compute_trade_fee(shop_ref_price: int, quantity: int) -> int:
    """Fee = 10% × ShopRefPrice × Quantity. Paid by buyer, goes to system sink."""
    return int(shop_ref_price * TRADE_FEE_RATE * quantity)


def buyer_total_cost(listing: MarketListing) -> int:
    """Total cost buyer pays = listing price + fee."""
    fee = compute_trade_fee(listing.shop_ref_price, listing.quantity)
    return listing.price + fee


def create_listing(
    seller_id: int,
    item_key: str,
    grade: Grade,
    quantity: int,
    price: int,
    shop_ref_price: int,
    listing_id: int,
) -> MarketListing:
    now = datetime.now(timezone.utc)
    return MarketListing(
        id=listing_id,
        seller_id=seller_id,
        item_key=item_key,
        grade=grade,
        quantity=quantity,
        price=price,
        shop_ref_price=shop_ref_price,
        currency_type=GRADE_CURRENCY[grade],
        expires_at=now + timedelta(hours=MARKET_LISTING_HOURS),
        created_at=now,
    )


def validate_listing(
    current_listings: int,
    seller_grade: Grade,
    item_grade: Grade,
) -> tuple[bool, str]:
    if current_listings >= MARKET_MAX_LISTINGS:
        return False, f"Đã đạt giới hạn {MARKET_MAX_LISTINGS} đơn hàng đang niêm yết."
    if seller_grade != item_grade:
        return False, "Chỉ có thể giao dịch vật phẩm cùng phẩm cấp."
    return True, ""
