"""Tests for the constitution-driven ``cultivation_speed_bonus``.

Multiplies EXP-per-turn for both the time-based axes (Luyện Thể / Luyện Khí
via ``advance_cultivation_xp``) and the merit-conversion axis (Trận Đạo via
``study_formation_with_merit``). 0.0 = no bonus, 1.0 = ×2 EXP.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.models.character import Character
from src.game.systems.cultivation import (
    advance_cultivation_xp, study_formation_with_merit,
)


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


# ── Synthetic, content-independent test bodies ───────────────────────────────
# The v12 roster rebuild emptied the real constitution roster, so this engine
# test injects its own bodies rather than reading shipped content:
#   • ``SynthNoSpeedBody``  — a neutral baseline with NO cultivation_speed_bonus
#     (the role the old ``ConstitutionVanTuong`` filler played here).
#   • ``SynthSpeedBody``    — carries ``cultivation_speed_bonus`` so the speed
#     multiplier can be exercised end-to-end (was ``ConstitutionTienThien…``).
_BASELINE = "SynthNoSpeedBody"
_SPEED = "SynthSpeedBody"
_SPEED_BONUS = 1.5


@pytest.fixture(autouse=True)
def _inject_synthetic_bodies(monkeypatch):
    monkeypatch.setitem(
        registry.constitutions,
        _BASELINE,
        {"key": _BASELINE, "stat_bonuses": {"hp_pct": 0.06, "mp_pct": 0.06}},
    )
    monkeypatch.setitem(
        registry.constitutions,
        _SPEED,
        {"key": _SPEED, "stat_bonuses": {"cultivation_speed_bonus": _SPEED_BONUS}},
    )


def make_char(**overrides) -> Character:
    defaults = dict(
        player_id=1, discord_id=123, name="Test",
        active_axis="qi",
        merit=100_000,
    )
    defaults.update(overrides)
    return Character(**defaults)


# ── advance_cultivation_xp (turn-based axes) ────────────────────────────────


def test_no_bonus_without_constitution():
    char = make_char(constitution_type=_BASELINE)
    result = advance_cultivation_xp(char, turns=10)
    baseline = result["exp_gained"]
    assert baseline > 0


def _speed_bonus() -> float:
    c = registry.get_constitution(_SPEED)
    return float(c["stat_bonuses"]["cultivation_speed_bonus"])


def test_full_speed_bonus_multiplies_exp():
    """A constitution carrying ``cultivation_speed_bonus`` should multiply
    the EXP earned per turn by ``(1 + bonus)``."""
    base_char = make_char(constitution_type=_BASELINE)
    base_exp = advance_cultivation_xp(base_char, turns=10)["exp_gained"]

    boosted_char = make_char(constitution_type=_SPEED)
    boosted_exp = advance_cultivation_xp(boosted_char, turns=10)["exp_gained"]
    expected = int(base_exp * (1.0 + _speed_bonus()))
    assert boosted_exp == expected
    assert boosted_exp > base_exp


def test_bonus_applies_to_body_axis_too():
    base_char = make_char(active_axis="body", constitution_type=_BASELINE)
    base_exp = advance_cultivation_xp(base_char, turns=10)["exp_gained"]

    boosted = make_char(active_axis="body", constitution_type=_SPEED)
    boosted_exp = advance_cultivation_xp(boosted, turns=10)["exp_gained"]
    expected = int(base_exp * (1.0 + _speed_bonus()))
    assert boosted_exp == expected


# ── study_formation_with_merit (merit-conversion axis) ──────────────────────


def test_formation_study_unaffected_without_bonus():
    char = make_char(constitution_type=_BASELINE, merit=10_000)
    result = study_formation_with_merit(char, merits=100)
    assert result["success"] is True
    base_exp = result["exp_gained"]
    assert base_exp > 0


def test_formation_study_amplified_by_speed_bonus():
    base = make_char(constitution_type=_BASELINE, merit=10_000)
    base_exp = study_formation_with_merit(base, merits=100)["exp_gained"]

    boosted = make_char(constitution_type=_SPEED, merit=10_000)
    boosted_exp = study_formation_with_merit(boosted, merits=100)["exp_gained"]
    expected = int(base_exp * (1.0 + _speed_bonus()))
    assert boosted_exp == expected
    assert boosted_exp > base_exp
