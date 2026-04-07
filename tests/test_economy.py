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
