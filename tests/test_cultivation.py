"""Tests for cultivation system."""
import pytest
from src.game.models.character import Character
from src.game.systems.cultivation import (
    can_breakthrough,
    apply_breakthrough,
    level_up,
    compute_hp_max,
    compute_mp_max,
)


def make_char() -> Character:
    return Character(player_id=1, discord_id=123, name="Test")


def test_cannot_breakthrough_before_level_9():
    char = make_char()
    char.qi_level = 5
    ok, msg = can_breakthrough(char, "qi")
    assert not ok
    assert "Cấp 9" in msg


def test_can_breakthrough_at_level_9():
    char = make_char()
    char.qi_level = 9
    char.qi_realm = 0
    ok, _ = can_breakthrough(char, "qi")
    assert ok


def test_breakthrough_advances_realm():
    char = make_char()
    char.qi_realm = 0
    char.qi_level = 9
    apply_breakthrough(char, "qi")
    assert char.qi_realm == 1
    assert char.qi_level == 1


def test_dao_ti_unlocked_on_body_breakthrough_nhap_thanh():
    char = make_char()
    char.body_realm = 7  # Siêu Phàm
    char.body_level = 9
    apply_breakthrough(char, "body")
    assert char.body_realm == 8  # Nhập Thánh
    assert char.dao_ti_unlocked is True


def test_hp_increases_with_body_level():
    char = make_char()
    hp1 = compute_hp_max(char)
    char.body_level = 9
    hp2 = compute_hp_max(char)
    assert hp2 > hp1


def test_mp_increases_with_formation_level():
    char = make_char()
    mp1 = compute_mp_max(char)
    char.formation_level = 9
    mp2 = compute_mp_max(char)
    assert mp2 > mp1
