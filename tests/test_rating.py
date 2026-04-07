"""Tests for rating → percentage conversion."""
import pytest
from src.game.engine.rating import crit_chance, evasion_chance, crit_dmg_multiplier, rating_to_pct


def test_rating_to_pct_example():
    # 300 CritRating, K=1300 → 18.75%
    assert abs(rating_to_pct(300) - 0.1875) < 0.001


def test_crit_chance_with_zero_rating():
    result = crit_chance(0)
    assert result == pytest.approx(0.05)  # base 5%


def test_crit_chance_with_res_reduces():
    base = crit_chance(300, 0)
    with_res = crit_chance(300, 300)
    assert with_res < base


def test_crit_chance_capped_at_75():
    assert crit_chance(100_000) <= 0.75


def test_evasion_zero():
    assert evasion_chance(0) == 0.0


def test_crit_dmg_base():
    assert crit_dmg_multiplier(0) == pytest.approx(1.5)
