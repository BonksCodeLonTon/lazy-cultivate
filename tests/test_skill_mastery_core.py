"""Phase 1 unit tests for the Skill Mastery pure core.

Pure-function tests over ``src.game.constants.skill_mastery`` and
``src.game.systems.skill_mastery`` — no combat fixtures, no DB, no registry.
The locked tables in the task brief are the source of truth.
"""
import pytest

from src.game.constants.skill_mastery import (
    BAND_LABELS,
    BAND_RANGES,
    GATES,
    HO_DAO_PHU_BONUS,
    MAX_LEVEL,
    POWER_MULT,
    VISIBLE_MAX,
    XP_TO_NEXT,
    MasteryBand,
)
from src.game.systems.skill_mastery import (
    apply_combat_xp,
    band_label,
    band_of,
    breakthrough_chance,
    hidden_gate_revealed,
    is_band_ceiling,
    power_mult,
    resolve_breakthrough,
    xp_to_next,
)


# --- Locked reference tables (source of truth) --------------------------------

_LOCKED_POWER_MULT = {
    1: 1.00, 2: 1.02, 3: 1.04, 4: 1.06, 5: 1.08,
    6: 1.13, 7: 1.15, 8: 1.17, 9: 1.19, 10: 1.22,
    11: 1.28, 12: 1.30, 13: 1.33, 14: 1.36, 15: 1.40,
    16: 1.47, 17: 1.50, 18: 1.53, 19: 1.57, 20: 1.62,
    21: 1.75, 22: 1.79, 23: 1.83, 24: 1.87, 25: 1.92,
}

_LOCKED_XP_WITHIN_BAND = {
    1: 20, 2: 30, 3: 45, 4: 60,
    6: 90, 7: 120, 8: 160, 9: 210,
    11: 280, 12: 360, 13: 460, 14: 580,
    16: 720, 17: 880, 18: 1080, 19: 1320,
    21: 1700, 22: 2100, 23: 2600, 24: 3200,
}


# --- power_mult ---------------------------------------------------------------

def test_power_mult_matches_locked_table():
    assert MAX_LEVEL == 25
    for level, expected in _LOCKED_POWER_MULT.items():
        assert power_mult(level) == pytest.approx(expected), f"level {level}"
        assert POWER_MULT[level] == pytest.approx(expected), f"POWER_MULT[{level}]"
    assert power_mult(1) == pytest.approx(1.0)


def test_power_mult_clamps():
    assert power_mult(0) == pytest.approx(1.0)
    assert power_mult(1) == pytest.approx(1.0)
    assert power_mult(0) == power_mult(1)
    assert power_mult(99) == pytest.approx(1.92)
    assert power_mult(25) == pytest.approx(1.92)
    assert power_mult(99) == power_mult(25)
    assert power_mult(-5) == pytest.approx(1.0)


def test_power_mult_monotonic_nondecreasing():
    for n in range(1, MAX_LEVEL):
        assert power_mult(n) <= power_mult(n + 1), f"non-monotonic at {n}"


# --- xp_to_next ---------------------------------------------------------------

def test_xp_to_next_within_band_matches_table():
    for level, expected in _LOCKED_XP_WITHIN_BAND.items():
        assert xp_to_next(level) == expected, f"level {level}"
        assert XP_TO_NEXT[level] == expected, f"XP_TO_NEXT[{level}]"


def test_xp_to_next_none_at_gates_and_max():
    for ceiling in (5, 10, 15, 20, 25):
        assert xp_to_next(ceiling) is None, f"ceiling {ceiling}"


# --- apply_combat_xp ----------------------------------------------------------

def test_apply_combat_xp_stops_at_band_ceiling():
    # From level 4, a big gain must stop exactly at the gated ceiling 5,
    # never crossing 5 -> 6, with overshoot discarded (xp returned == 0).
    new_level, new_xp, at_ceiling = apply_combat_xp(4, 0, 100_000)
    assert new_level == 5
    assert at_ceiling is True
    assert new_xp == 0
    assert is_band_ceiling(new_level) is True


def test_apply_combat_xp_multi_level_within_band():
    # 1 -> 2 costs 20, 2 -> 3 costs 30 (total 50). Give 55: land at level 3
    # with 5 leftover, still inside band 1 (no ceiling hit).
    new_level, new_xp, at_ceiling = apply_combat_xp(1, 0, 55)
    assert new_level == 3
    assert new_xp == 5
    assert at_ceiling is False


def test_apply_combat_xp_caps_at_max_level():
    new_level, new_xp, at_ceiling = apply_combat_xp(24, 0, 1_000_000)
    assert new_level == MAX_LEVEL == 25
    assert at_ceiling is True
    assert new_level <= MAX_LEVEL
    assert new_xp == 0


# --- breakthrough_chance ------------------------------------------------------

def test_breakthrough_chance_base_pity_talisman():
    # gate 10: base 0.70, pity 0.08/fail, talisman +0.20
    assert breakthrough_chance(10, 0, False) == pytest.approx(0.70)
    assert breakthrough_chance(10, 1, False) == pytest.approx(0.78)
    assert breakthrough_chance(10, 0, True) == pytest.approx(0.90)
    # gate 5: base 0.90
    assert breakthrough_chance(5, 0, False) == pytest.approx(0.90)
    assert HO_DAO_PHU_BONUS == pytest.approx(0.20)


def test_breakthrough_chance_caps_at_one():
    # gate 15: base 0.50, pity 0.10/fail. 20 fails -> 0.50 + 2.0 + 0.20 talisman,
    # clamped to exactly 1.0.
    assert breakthrough_chance(15, 20, True) == pytest.approx(1.0)


# --- resolve_breakthrough -----------------------------------------------------

def test_resolve_breakthrough_success():
    gate = 10
    chance = breakthrough_chance(gate, 0, False)
    result = resolve_breakthrough(gate, 0, False, False, roll=chance - 0.01)
    assert result["success"] is True
    assert result["new_level"] == gate + 1
    assert result["new_fails"] == 0
    assert result["consumed_gate_qty"] == GATES[gate]["qty"]
    assert result["refunded"] is False


def test_resolve_breakthrough_fail_consumes_stack():
    gate = 10
    fails = 2
    chance = breakthrough_chance(gate, fails, False)
    result = resolve_breakthrough(gate, fails, False, False, roll=chance + 0.05)
    assert result["success"] is False
    assert result["new_level"] == gate  # level unchanged
    assert result["new_fails"] == fails + 1
    assert result["consumed_gate_qty"] == GATES[gate]["qty"]
    assert result["refunded"] is False


def test_resolve_breakthrough_fail_with_dinh_dao_chau_refunds():
    gate = 15
    fails = 1
    chance = breakthrough_chance(gate, fails, True)
    # Fail with Định Đạo Châu + Hộ Đạo Phù supplied.
    result = resolve_breakthrough(
        gate, fails, ho_dao_phu=True, dinh_dao_chau=True, roll=chance + 0.05
    )
    assert result["success"] is False
    assert result["refunded"] is True
    assert result["consumed_gate_qty"] == 0
    assert result["consumed_dinh_dao_chau"] is True
    assert result["consumed_ho_dao_phu"] is True

    # And when ho_dao_phu is NOT passed, the flag reflects that.
    chance_no_talisman = breakthrough_chance(gate, fails, False)
    result2 = resolve_breakthrough(
        gate, fails, ho_dao_phu=False, dinh_dao_chau=True, roll=chance_no_talisman + 0.05
    )
    assert result2["consumed_ho_dao_phu"] is False
    assert result2["refunded"] is True


# --- hidden_gate_revealed -----------------------------------------------------

def test_hidden_gate_revealed():
    assert VISIBLE_MAX == 20
    assert hidden_gate_revealed(20, True, True) is True
    # Flip each of the three requirements independently.
    assert hidden_gate_revealed(19, True, True) is False  # level != 20
    assert hidden_gate_revealed(20, False, True) is False  # no fruit
    assert hidden_gate_revealed(20, True, False) is False  # dao_ti not unlocked


# --- band_of / band_label -----------------------------------------------------

def test_band_of_boundaries():
    # So Khuy (1-5) -> Tieu Thanh (6-10)
    assert band_of(5) == MasteryBand.SO_KHUY
    assert band_of(6) == MasteryBand.TIEU_THANH
    assert band_of(5) != band_of(6)
    # Vien Man (16-20) -> Dang Phong Tao Cuc (21-25)
    assert band_of(20) == MasteryBand.VIEN_MAN
    assert band_of(21) == MasteryBand.DANG_PHONG_TAO_CUC
    assert band_of(20) != band_of(21)
    # Ranges agree with the constant table.
    assert BAND_RANGES[MasteryBand.SO_KHUY] == (1, 5)
    assert BAND_RANGES[MasteryBand.DANG_PHONG_TAO_CUC] == (21, 25)
    # Spot-check a mid-band label tuple (level 13 -> Dai Thanh).
    assert band_label(13) == BAND_LABELS[MasteryBand.DAI_THANH]
    assert band_label(13) == ("Đại Thành", "Great Mastery")
