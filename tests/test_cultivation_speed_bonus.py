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
    char = make_char(constitution_type="ConstitutionVanTuong")
    result = advance_cultivation_xp(char, turns=10)
    baseline = result["exp_gained"]
    assert baseline > 0


def _tien_thien_speed_bonus() -> float:
    c = registry.get_constitution("ConstitutionTienThienThanhTheDaoThai")
    return float(c["stat_bonuses"]["cultivation_speed_bonus"])


def test_full_speed_bonus_multiplies_exp():
    """A constitution carrying ``cultivation_speed_bonus`` should multiply
    the EXP earned per turn by ``(1 + bonus)``."""
    base_char = make_char(constitution_type="ConstitutionVanTuong")
    base_exp = advance_cultivation_xp(base_char, turns=10)["exp_gained"]

    boosted_char = make_char(
        constitution_type="ConstitutionTienThienThanhTheDaoThai",
    )
    boosted_exp = advance_cultivation_xp(boosted_char, turns=10)["exp_gained"]
    expected = int(base_exp * (1.0 + _tien_thien_speed_bonus()))
    assert boosted_exp == expected
    assert boosted_exp > base_exp


def test_bonus_applies_to_body_axis_too():
    base_char = make_char(active_axis="body", constitution_type="ConstitutionVanTuong")
    base_exp = advance_cultivation_xp(base_char, turns=10)["exp_gained"]

    boosted = make_char(
        active_axis="body",
        constitution_type="ConstitutionTienThienThanhTheDaoThai",
    )
    boosted_exp = advance_cultivation_xp(boosted, turns=10)["exp_gained"]
    expected = int(base_exp * (1.0 + _tien_thien_speed_bonus()))
    assert boosted_exp == expected


# ── study_formation_with_merit (merit-conversion axis) ──────────────────────


def test_formation_study_unaffected_without_bonus():
    char = make_char(constitution_type="ConstitutionVanTuong", merit=10_000)
    result = study_formation_with_merit(char, merits=100)
    assert result["success"] is True
    base_exp = result["exp_gained"]
    assert base_exp > 0


def test_formation_study_amplified_by_speed_bonus():
    base = make_char(constitution_type="ConstitutionVanTuong", merit=10_000)
    base_exp = study_formation_with_merit(base, merits=100)["exp_gained"]

    boosted = make_char(
        constitution_type="ConstitutionTienThienThanhTheDaoThai",
        merit=10_000,
    )
    boosted_exp = study_formation_with_merit(boosted, merits=100)["exp_gained"]
    expected = int(base_exp * (1.0 + _tien_thien_speed_bonus()))
    assert boosted_exp == expected
    assert boosted_exp > base_exp


# ── Registry: the new constitution exists and is wired ──────────────────────


def test_tien_thien_constitution_is_registered():
    c = registry.get_constitution("ConstitutionTienThienThanhTheDaoThai")
    assert c is not None
    assert c["rarity"] == "legendary"
    assert c["element"] is None
    bonuses = c["stat_bonuses"]
    # "Massively boost" — at least +100% (×2 EXP). The exact value is a
    # designer-tunable knob; this test just enforces the floor.
    assert bonuses["cultivation_speed_bonus"] >= 1.0
    # Sanity-check the "large stats" claim — at least these key categories present
    assert bonuses.get("hp_pct", 0) > 0
    assert bonuses.get("mp_pct", 0) > 0
    assert bonuses.get("final_dmg_bonus", 0) > 0
    assert bonuses.get("final_dmg_reduce", 0) > 0
    assert bonuses.get("crit_rating", 0) > 0
    assert bonuses.get("res_all", 0) > 0
