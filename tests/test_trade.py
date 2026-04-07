"""Tests for P2P trade system."""
import pytest
from src.game.constants.grades import Grade
from src.game.systems.trade import compute_trade_fee, buyer_total_cost, create_listing, validate_listing


def test_trade_fee_10_pct():
    fee = compute_trade_fee(shop_ref_price=300, quantity=5)
    assert fee == 150  # 300 × 10% × 5


def test_buyer_total_cost():
    listing = create_listing(
        seller_id=1,
        item_key="GemKim",
        grade=Grade.HUYEN,
        quantity=5,
        price=1500,
        shop_ref_price=300,
        listing_id=1,
    )
    assert buyer_total_cost(listing) == 1500 + 150  # 1650


def test_validate_listing_max_exceeded():
    ok, msg = validate_listing(current_listings=5, seller_grade=Grade.HUYEN, item_grade=Grade.HUYEN)
    assert not ok
    assert "giới hạn" in msg


def test_validate_listing_grade_mismatch():
    ok, msg = validate_listing(current_listings=0, seller_grade=Grade.HOANG, item_grade=Grade.HUYEN)
    assert not ok
    assert "cùng phẩm cấp" in msg


def test_validate_listing_ok():
    ok, msg = validate_listing(current_listings=2, seller_grade=Grade.DIA, item_grade=Grade.DIA)
    assert ok
    assert msg == ""
