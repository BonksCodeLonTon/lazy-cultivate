"""Tests for turn system and currency economy."""
from datetime import datetime, timedelta, timezone
import pytest
from src.game.models.character import Character
from src.game.engine.tick import compute_offline_ticks
from src.game.constants.currencies import CURRENCY_CAP, MERIT_PER_BONUS_TURN, MERIT_PER_NORMAL_TURN


def make_char() -> Character:
    return Character(player_id=1, discord_id=123, name="Test")


def test_bonus_turns_give_more_merit():
    char = make_char()
    char.bonus_turns_remaining = 10
    char.turns_today = 0
    last_tick = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = compute_offline_ticks(char, last_tick)
    # 5 bonus turns → 5 × 2 = 10 merit, 0 karma
    assert result["merit_gained"] == 5 * MERIT_PER_BONUS_TURN
    assert result["karma_gained"] == 0


def test_normal_turns_give_karma():
    char = make_char()
    char.bonus_turns_remaining = 0
    char.turns_today = 0
    last_tick = datetime.now(timezone.utc) - timedelta(minutes=3)
    result = compute_offline_ticks(char, last_tick)
    assert result["karma_gained"] == 3 * 7  # KARMA_PER_NORMAL_TURN = 7


def test_merit_capped():
    char = make_char()
    char.merit = CURRENCY_CAP - 1
    char.bonus_turns_remaining = 10
    last_tick = datetime.now(timezone.utc) - timedelta(minutes=60)
    compute_offline_ticks(char, last_tick)
    assert char.merit == CURRENCY_CAP


def test_evil_title_at_threshold():
    char = make_char()
    char.bonus_turns_remaining = 0
    char.karma_accum = 99_000
    last_tick = datetime.now(timezone.utc) - timedelta(minutes=200)
    result = compute_offline_ticks(char, last_tick)
    assert char.evil_title == "van_ac_bat_xa"


# ── Dark market sampling ─────────────────────────────────────────────────

def test_get_dark_market_handles_small_pool(monkeypatch):
    """When ``DARK_POOL`` shrinks below 5 entries the old
    ``randint(5, min(8, len(pool)))`` bound inverts to ``randint(5, 4)``
    and crashes every Quỷ Thị render. The fix floors the lower bound at
    the actual pool size — pin the regression here so a future trim
    doesn't re-introduce the same crash."""
    from src.game.systems import economy
    monkeypatch.setattr(economy, "DARK_POOL", economy.DARK_POOL[:4])
    fixed, rotating = economy.get_dark_market(seed=42)
    assert fixed is not None
    assert 1 <= len(rotating) <= 4
    # Distinct items only (sample, not choice).
    assert len({s.item_key for s in rotating}) == len(rotating)


def test_get_dark_market_returns_empty_when_pool_drained(monkeypatch):
    from src.game.systems import economy
    monkeypatch.setattr(economy, "DARK_POOL", [])
    fixed, rotating = economy.get_dark_market(seed=0)
    assert fixed is not None
    assert rotating == []


def test_get_dark_market_full_pool_uses_legacy_5_to_8_band(monkeypatch):
    """When the pool is large enough, sampling stays in the original 5-8
    range — the small-pool fix must not regress the normal-size path."""
    from src.game.systems import economy
    big_pool = [{"item_key": f"X{i}", "grade": 1, "price": 1, "currency": "merit"}
                for i in range(20)]
    monkeypatch.setattr(economy, "DARK_POOL", big_pool)
    sizes = {len(economy.get_dark_market(seed=s)[1]) for s in range(50)}
    assert sizes <= {5, 6, 7, 8}, f"sample sizes drifted out of band: {sizes}"
