"""Tests for the EXP-based cultivation system (docs/data.xlsx spec)."""
import pytest

from src.game.constants.realms import BODY_REALMS, QI_REALMS, TRIBULATION_EXP_COST
from src.game.models.character import Character
from src.game.systems.cultivation import (
    advance_cultivation_xp,
    apply_breakthrough,
    can_breakthrough,
    compute_hp_max,
    compute_mp_max,
    study_formation_with_merit,
)


def make_char() -> Character:
    # Pin to the empty constitution so cultivation-rate tests measure the
    # baseline EXP math with no ``cultivation_speed_bonus`` from the new
    # Phàm Thể default.
    return Character(player_id=1, discord_id=123, name="Test", constitution_type="")


def test_cannot_breakthrough_below_realm_max_xp():
    char = make_char()
    char.qi_realm = 0
    char.qi_xp = QI_REALMS[0].level_exp_table[-1] - 1
    ok, msg = can_breakthrough(char, "qi")
    assert not ok
    assert "EXP" in msg


def test_can_breakthrough_at_realm_max_xp():
    char = make_char()
    char.qi_realm = 0
    char.qi_xp = QI_REALMS[0].level_exp_table[-1]
    ok, _ = can_breakthrough(char, "qi")
    assert ok


def test_breakthrough_advances_realm_and_resets_xp():
    char = make_char()
    char.qi_realm = 0
    char.qi_xp = QI_REALMS[0].level_exp_table[-1]
    apply_breakthrough(char, "qi")
    assert char.qi_realm == 1
    assert char.qi_level == 1
    assert char.qi_xp == 0


def test_dao_ti_unlocked_on_body_breakthrough_nhap_thanh():
    char = make_char()
    char.body_realm = 7  # Siêu Phàm
    char.body_xp = BODY_REALMS[7].level_exp_table[-1]
    apply_breakthrough(char, "body")
    assert char.body_realm == 8  # Nhập Thánh
    assert char.dao_ti_unlocked is True


def test_hp_increases_with_body_level():
    char = make_char()
    hp1 = compute_hp_max(char)
    char.body_level = 9
    hp2 = compute_hp_max(char)
    assert hp2 > hp1


def test_hp_flat_per_realm_scales_with_max_realm():
    char = make_char()
    char.body_realm = 0
    char.qi_realm = 0
    char.formation_realm = 0
    base = compute_hp_max(char, {"hp_flat_per_realm": 500})

    char.body_realm = 4  # max_realm = 4
    with_realm = compute_hp_max(char, {"hp_flat_per_realm": 500})
    assert with_realm - compute_hp_max(char, {}) == pytest.approx(500 * 4)
    assert with_realm > base


def test_hp_flat_per_realm_uses_max_axis():
    """Bonus follows the highest realm across body/qi/formation, not just body."""
    char = make_char()
    char.body_realm = 0
    char.qi_realm = 6
    char.formation_realm = 2
    bare = compute_hp_max(char, {})
    with_flat = compute_hp_max(char, {"hp_flat_per_realm": 1000})
    assert with_flat - bare == 1000 * 6


def test_hp_flat_per_realm_zero_at_realm_zero():
    char = make_char()  # all realms = 0
    bare = compute_hp_max(char, {})
    with_flat = compute_hp_max(char, {"hp_flat_per_realm": 1000})
    assert with_flat == bare


def test_hp_flat_per_realm_stacks_after_pct_multiplier():
    """Flat bonus is additive *after* the hp_pct multiplier — same field treated
    independently per the design (pct scales the cultivation base, flat adds
    a fixed chunk per realm)."""
    char = make_char()
    char.body_realm = 5
    bare = compute_hp_max(char, {})
    pct_only = compute_hp_max(char, {"hp_pct": 0.20})
    both = compute_hp_max(char, {"hp_pct": 0.20, "hp_flat_per_realm": 500})
    assert both - pct_only == 500 * 5
    assert pct_only > bare


def test_mp_increases_with_formation_level():
    char = make_char()
    mp1 = compute_mp_max(char)
    char.formation_level = 9
    mp2 = compute_mp_max(char)
    assert mp2 > mp1


def test_advance_cultivation_earns_exp_per_realm_rate():
    char = make_char()
    char.active_axis = "qi"
    char.qi_realm = 2  # Kim Đan, rate 3
    char.qi_xp = 0
    result = advance_cultivation_xp(char, 100)
    assert result["exp_gained"] == 300
    assert char.qi_xp == 300


def test_advance_cultivation_levels_up_via_realm_table():
    char = make_char()
    char.active_axis = "qi"
    char.qi_realm = 0  # Luyện Khí
    char.qi_xp = 0
    # Bậc 2 threshold = 267; 270 turns at rate 1 = 270 xp ≥ 267.
    result = advance_cultivation_xp(char, 270)
    assert char.qi_xp == 270
    assert char.qi_level == 2
    assert result["levels_gained"] >= 1


def test_formation_earns_no_exp_from_turns():
    char = make_char()
    char.active_axis = "formation"
    result = advance_cultivation_xp(char, 500)
    assert result["exp_gained"] == 0
    assert char.formation_xp == 0


def test_study_formation_with_merit_converts_exp_and_levels():
    char = make_char()
    char.merit = 1_000
    char.formation_realm = 1  # Nhập Huyền, table max 7200
    result = study_formation_with_merit(char, 80)  # 80 merit × 10 = 800 xp
    assert result["success"]
    assert char.merit == 920
    assert char.formation_xp == 800
    # Nhập Huyền level table step 800 → 800 xp crosses bậc-1 threshold.
    assert char.formation_level == 1


def test_final_realm_breakthrough_gate_matches_tribulation_cost():
    """Realm 8 (endgame) bậc-9 threshold equals TRIBULATION_EXP_COST."""
    assert QI_REALMS[8].level_exp_table[-1] == TRIBULATION_EXP_COST
    char = make_char()
    char.qi_realm = 8
    char.qi_xp = TRIBULATION_EXP_COST
    ok, _ = can_breakthrough(char, "qi")
    assert ok
